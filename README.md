# homelab-docs

Production-style runbooks for a multi-site homelab: three Proxmox hosts across
two sites, ZFS storage on TrueNAS SCALE, OPNsense edge routing over XGS-PON
fiber, verified off-site backups, and a containerized service stack.

These are working documents, not tutorials. Each one records what was actually
built, why the approach was chosen, what broke along the way, and how to rebuild
it from nothing.

---

## Runbooks

| Doc | Covers |
| --- | --- |
| [TRUENAS.md](TRUENAS.md) | 30 TB storage migration — mdadm RAID5 → TrueNAS SCALE / ZFS RAIDZ1, with PCIe SATA controller passthrough, pool and dataset design, and dual SMB/NFS shares under a unified identity |
| [SMART-DOCTOR.md](SMART-DOCTOR.md) | Using smartmontools to establish a base health for the 4 x 10TB NAS drives.
| [UPS.md](UPS.md) | UPS monitoring conversion from PowerPanel (`pwrstat`) to NUT |
| [TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md) | Why the TrueNAS reporting page stays blank when NUT runs in netclient mode (NAS-132924) — the charts.d module that assumes a local `upsd`, a config override that fixes it without touching the immutable rootfs, and an init script to survive OS upgrades |
| [UPTIME-KUMA.md](UPTIME-KUMA.md) | Declarative availability monitoring — monitors defined in YAML and reconciled into Uptime Kuma by a Python script, so the monitor set is version-controlled rather than click-configured |
| [DNS.md](DNS.md) | Redundant Pi-hole resolvers in separate failure domains, advertised by Kea DHCPv4 option 6 — address-space layout, Teleporter parity, the static-host audit procedure, and the failure modes DHCP resolver lists actually have |
| [ODROID-XU4.md](ODROID-XU4.md) | Rebuilding the DNS host off an orphaned Hardkernel vendor kernel onto maintained Armbian — why a release upgrade was rejected, U-Boot's fixed-filename boot flow, and the eMMC reflash with a rollback path |
| [PIHOLE-DOCKER.md](PIHOLE-DOCKER.md) | First-build procedure for the primary Pi-hole container on bare Armbian — freeing :53 from systemd-resolved, armhf image constraints, compose layout, and the initial Teleporter import |
| [UNBOUND.md](UNBOUND.md) | Per-host recursive, DNSSEC-validating resolver replacing Quad9 as upstream on both Pi-holes — install, validation, cutover, and rollback |
| [WIREGUARD.md](WIREGUARD.md) | Multi-site overlay network — hub-and-spoke WireGuard through a cloud instance so neither residential endpoint needs inbound reachability, with subnet routing to a second site and full-tunnel roaming clients |

---

## Conventions

Every doc follows the same shape: a summary block up top, numbered sections, a
"known limitations" or "caveats" section where the honest tradeoffs go, and a
**Quick reference** table at the end for the things you actually look up at 2am.

Sanitization is enforced by a pre-commit hook rather than by memory:

| Rule |
| --- |
| Internal addresses masked as `10.x.x.N` |
| Secrets referenced as `${VAR}` or `<placeholder>`, never literals |
| WAN / ISP / ONT / MAC details never committed |

The hook lives at [`scripts/pre-commit`](scripts/pre-commit) and blocks commits
containing unmasked addresses, hardcoded credentials, private keys, tokens, MAC
addresses, or staged `.env` files. Install it after cloning — git hooks aren't
carried by a clone:

```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

---

## In progress

- Ansible roles for fleet configuration (baseline, NUT, restic, Docker hosts)
- Prometheus + Grafana + node_exporter for metrics alongside Uptime Kuma's up/down
- VLAN segmentation on the CRS310, replacing the current flat L2 network
- Terraform for Proxmox VM provisioning
