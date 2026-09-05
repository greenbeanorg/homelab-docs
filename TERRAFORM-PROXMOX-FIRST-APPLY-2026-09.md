# Terraform + Proxmox: First Apply, Cert Chain, and Token Permission Scoping

**Date:** 2026-09-05
**Host:** swearengen (Proxmox VE 9.2.11)
**Control node:** dority (Terraform, `bpg/proxmox` provider)
**Outcome:** Working end-to-end LXC provisioning pipeline from dority → swearengen API, with a correctly scoped, least-privilege `terraform@pve` token.

---

## 1. Goal

Stand up Terraform on `dority` as a control node, targeting `swearengen`'s Proxmox API, and provision a throwaway LXC (`tf-test-01`, VMID 950) to validate the pipeline before building anything real on top of it (k3s, Ansible-configured hosts, etc).

## 2. Summary of what broke, in order

This ended up being five distinct issues stacked on top of each other. Each one only became visible after the prior one was fixed — Proxmox and TLS both fail fast on the *first* problem rather than reporting everything up front, so this looked like a single problem five separate times.

1. Stale TLS SAN (`.lan` domain no longer in use)
2. Node hostname itself still set to the old `.lan` FQDN
3. Cert regeneration required a `pveproxy` restart to actually take effect
4. Self-signed CA not trusted by dority
5. API token permissions: three separate missing privilege domains (VM lifecycle, SDN, storage)

## 3. Issue 1–3: Hostname and cert drift

**Symptom:**
```
tls: failed to verify certificate: x509: certificate is valid for localhost, swearengen,
swearengen.lan.greenbean.org, not swearengen.greenbean.org
```

**Root cause:** `greenbean.org`'s DNS scheme dropped the `.lan` subdomain at some point, but `swearengen`'s own `hostname -f` and `/etc/hosts` were never updated to match. The Proxmox self-signed cert derives its SAN list from the node's own hostname resolution, so it kept minting certs for the dead `.lan` name.

**Fix:**
```bash
# confirm current (wrong) state
hostname -f

# fix hostname
sudo hostnamectl set-hostname swearengen.greenbean.org
sudo vi /etc/hostname       # confirm it matches
sudo vi /etc/hosts          # confirm no stale .lan entries active (commented is fine)

# regenerate cert AND restart the services that serve it
sudo systemctl restart pvedaemon pveproxy
sudo rm /etc/pve/local/pve-ssl.key /etc/pve/local/pve-ssl.pem
sudo pvecm updatecerts -f
sudo systemctl restart pveproxy

# verify
openssl s_client -connect swearengen.greenbean.org:8006 \
  -servername swearengen.greenbean.org </dev/null 2>/dev/null \
  | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"
```

⚠️ **Gotcha:** `pvecm updatecerts -f` alone is not sufficient. The first regeneration attempt in this session produced a cert that *still* listed the old SAN, because `pveproxy` was serving a cached copy from before the restart. Restarting `pveproxy` (and `pvedaemon`, to be safe) *before* regenerating is what actually got a clean result.

⚠️ **Gotcha:** `pveam list local` and other cluster-aware commands fail with `ipcc_send_rec[N] failed: Unknown error -1` when not run as root. Not a real error — just needs `sudo`.

## 4. Issue 4: Untrusted self-signed CA

**Symptom (after hostname/cert fix):**
```
tls: failed to verify certificate: x509: certificate signed by unknown authority
```

**Root cause:** Once the SAN matched, the remaining problem was that dority had never been told to trust Proxmox's self-signed root CA at all.

**Fix (on dority):**
```bash
sudo scp aba@swearengen:/etc/pve/pve-root-ca.pem /usr/local/share/ca-certificates/pve-root-ca.crt
sudo update-ca-certificates
```

⚠️ **Gotcha:** the destination file must end in `.crt` — `update-ca-certificates` on Debian only picks up files with that extension from `/usr/local/share/ca-certificates/`.

This is a one-time trust operation per control node. Since `pve-root-ca.pem` is the same CA that will sign `wu`'s node cert too (and any future nodes), this doesn't need to be repeated per-host — just per Terraform control node.

## 5. Issue 5: API token permission scoping (three sub-issues)

Once TLS was fully resolved, container creation began actually reaching Proxmox's authorization layer — and failed three separate times, once per privilege domain touched by an LXC create.

### 5a. Token vs. user permission intersection

**Critical concept, not obvious from the UI:** per Proxmox's own docs, *"Permissions on API tokens are always a subset of those of their corresponding user."* With privilege separation enabled (the default), a token's **effective** permission set is the *intersection* of:
- whatever ACL is granted to the token itself, and
- whatever ACL is granted to the backing user

Granting a role to the token alone, while the backing user (`terraform@pve`) has zero ACL entries of its own, results in an effective permission set of **nothing** — even though `pveum acl list` shows the token's grant looking completely correct.

**Diagnostic:**
```bash
pveum user token permissions terraform@pve terraform --path / --output-format json-pretty
```
This showed `{}` even after confirming via `pveum acl list` that the token had `PVEVMAdmin` on `/`. That mismatch is the tell.

**Fix pattern (repeated for each permission domain below):** grant the *same* role to both the user and the token:
```bash
pveum acl modify <path> --users 'terraform@pve' --roles <role>
pveum acl modify <path> --tokens 'terraform@pve!terraform' --roles <role>
```

### 5b. VM lifecycle — `VM.PowerMgmt`

**Symptom:** `Permission check failed (/vms/950, VM.PowerMgmt)`
**Fix:** `PVEVMAdmin` on `/`, applied to both user and token (per 5a pattern). `PVEVMAdmin` does include `VM.PowerMgmt` in its role definition — the failure was purely the user/token intersection issue in 5a, not a role choice problem.

### 5c. SDN — `SDN.Use`

**Symptom:** `Permission check failed (/sdn/zones/localnetwork/vmbr0/20, SDN.Use)`
**Cause:** Assigning a VLAN tag to a container's network interface requires SDN privileges, which `PVEVMAdmin` does not include.
**Fix:** `PVESDNUser` on `/`, applied to both user and token.

### 5d. Storage — `Datastore.AllocateSpace`

**Symptom:** `Permission check failed (/storage/local-lvm, Datastore.AllocateSpace)`
**Cause:** Allocating the container's disk on `local-lvm` needs datastore privileges. Neither prior role covers this.
**Fix:** `PVEDatastoreAdmin` scoped to `/storage` (not root — this is the one grant intentionally scoped narrower than `/`), applied to both user and token.

### Final working permission set for `terraform@pve` / `terraform@pve!terraform`

| Path | Role | Covers |
|---|---|---|
| `/` | `PVEVMAdmin` | VM/container lifecycle, config, power management |
| `/` | `PVESDNUser` | VLAN/network zone assignment |
| `/storage` | `PVEDatastoreAdmin` | Disk allocation on datastores |

Both the **user** and the **token** need each grant — granting only one half is the single most likely mistake to make here, and it fails silently (empty permission set, not an error) rather than loudly.

## 6. LXC-specific gotchas hit along the way

- **`unprivileged = false` is the provider default.** Creating a *privileged* container via a non-root, scoped token generally isn't possible even with `PVEVMAdmin` — privileged containers have host-level implications Proxmox restricts more tightly. Set `unprivileged = true` explicitly in the Terraform resource unless you have a specific reason for a privileged container.
- **Debian 12 was not actually available locally** despite being referenced in early drafts of the `.tf` file — only `debian-13-standard_13.6-1_amd64.tar.zst` was cached (`pveam list local`, run as root). Check what's actually cached before writing `template_file_id`, don't assume a version exists.
- **A failed `terraform apply` mid-creation can still leave the LXC's config on Proxmox** even though Terraform's own state file has no record of it (since the API call that would have returned the resource ID never completed). Always check `qm list` / `pct list` for the VMID in question before retrying, to avoid a VMID collision on the next attempt. In this session, no orphan was left behind, but it's worth the check every time.
- **"Systemd 257 detected. You may need to enable nesting."** — a warning, not an error, shown on `apply` completion. Only matters if you plan to run Docker or other container-in-container workloads inside this LXC later.

## 7. Quick reference — reproducing this setup on a new control node

```bash
# 1. Trust the Proxmox CA
sudo scp aba@swearengen:/etc/pve/pve-root-ca.pem /usr/local/share/ca-certificates/pve-root-ca.crt
sudo update-ca-certificates

# 2. Install Terraform (apt.releases.hashicorp.com repo)

# 3. providers.tf points at https://swearengen.greenbean.org:8006/
#    (confirm this hostname is actually in the current cert's SAN list first —
#     see openssl s_client command in Section 3)

# 4. Confirm terraform@pve user + token both carry:
pveum user token permissions terraform@pve terraform --path / --output-format json-pretty
pveum user token permissions terraform@pve terraform --path /storage --output-format json-pretty
# Expect: full VM.* set, SDN.Audit/SDN.Use at /, Datastore.* set at /storage
```

## 8. Honest caveats

- The final permission set was arrived at reactively (fail → diagnose → grant → retry), not planned in advance. It works and is reasonably scoped, but hasn't been audited against the *full* list of privileges an LXC create touches — it's possible a different resource type (a full VM instead of an LXC, or a container using different storage/network features) surfaces a fourth or fifth gap not covered here.
- `/storage` grant is datacenter-wide across all storage pools, not scoped to `local-lvm` specifically. Tightening this further is possible (`/storage/local-lvm`) but wasn't done in this session.
- This was tested against LXC creation only. VM (QEMU) creation via the same token/role set has not yet been verified and may need additional grants (e.g. `VM.Config.Disk` is already covered, but ISO/cloud-init image handling for VMs may touch different storage paths).

