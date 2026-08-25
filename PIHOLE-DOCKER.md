# Dockerized Pi-hole on ODROID-XU4 (Redundant DNS)

**Status:** Complete — serving as primary network-wide resolver
**Host:** ODROID-XU4 (Armbian / Ubuntu 26.04 armhf) at `10.x.x.250`
**Companion (secondary):** script-installed Pi-hole in an LXC on `swearengen` at `10.x.x.249`

## Goal

Stand up a second network-wide DNS resolver so either Pi-hole can be rebooted, updated, or
fail without taking DNS down for the house. This instance takes over as **primary** — a
dedicated single-purpose board is a better front-line resolver than an LXC sharing
`swearengen` with everything else. The existing `.249` instance drops to **secondary**.

Containerized this time: `.249` was installed with the `curl | bash` installer, which is
harder to rebuild reproducibly.

Follows the [XU4 Armbian migration](XU4-ARMBIAN.md), which put a
current OS on the board.

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Deployment | Docker Compose, `network_mode: host` | Container must own :53 on a real LAN address; host mode avoids Docker's NAT layer for DNS |
| Address | Host's own static IP (`10.x.x.250`) | Host networking binds host interfaces — there is no per-container IP without macvlan |
| Upstreams | Quad9 (`9.9.9.9;149.112.112.112`) | Set via env var, not the imported config — see the override gotcha below |
| Config source | Teleporter import from `.249` | Carries adlists, local DNS records, CNAMEs, groups in one shot |
| Web UI | HTTPS on `443s`, self-signed | Admin traffic stays on a trusted LAN; no public CA can issue for RFC1918 anyway |
| Redundancy | `.250` primary, `.249` secondary, both handed out via DHCP | Either resolver can go down without an outage |

## 1. ⚠️ Free port 53 from systemd-resolved

On a fresh Armbian install, `systemd-resolved` runs a stub listener and `/etc/resolv.conf`
points at `127.0.0.1`. Before the container starts:

```
# ss -lntup | grep ':53'
udp UNCONN 0 0    127.0.0.54:53 0.0.0.0:* users:(("systemd-resolve",...))
udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",...))
tcp LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",...))
tcp LISTEN 0 4096    127.0.0.54:53 0.0.0.0:* users:(("systemd-resolve",...))
```

Disable the stub listener and point the host at an upstream resolver:

```bash
mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/no-stub.conf << 'EOF'
[Resolve]
DNSStubListener=no
DNS=10.x.x.249
EOF
systemctl restart systemd-resolved
ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
ss -lntup | grep ':53'   # expect empty
```

⚠️ **Point the host at the *other* Pi-hole, not itself.** If the host resolves through its
own container, a failed container start means a box that can't resolve — including the DNS
lookups needed to pull a fresh image. Even though this box is the network's primary
resolver, it should itself resolve via `.249`.

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version
```

⚠️ **armhf image availability.** The XU4 is ARMv7/armhf. Pi-hole publishes `linux/arm/v7`
images, so this works — but many modern images are arm64/amd64 only. A `no matching
manifest` error on this board means the project dropped 32-bit ARM.

## 3. Compose file

`/opt/pihole/docker-compose.yml`:

```yaml
services:
  pihole:
    container_name: pihole
    image: pihole/pihole:latest
    network_mode: host
    environment:
      TZ: 'America/New_York'
      FTLCONF_webserver_api_password: 'REDACTED'
      FTLCONF_dns_listeningMode: 'all'
      FTLCONF_dns_upstreams: '9.9.9.9;149.112.112.112'
      FTLCONF_webserver_port: '443s'
    volumes:
      - './etc-pihole:/etc/pihole'
    cap_add:
      - NET_ADMIN
      - SYS_TIME
      - SYS_NICE
    restart: unless-stopped
```

```bash
mkdir -p /opt/pihole && cd /opt/pihole
docker compose up -d
docker compose logs -f
```

Notes:
- **`443s`** — the `s` suffix is civetweb syntax for a TLS port. Listing only `443s` disables
  plain HTTP. Pi-hole auto-generates a self-signed cert at `/etc/pihole/tls.pem` on first
  start; browsers warn, which is acceptable for LAN-only admin access.
- Use `'80,443s'` during initial setup if there's any risk of locking yourself out of a
  headless box, then drop `80` once HTTPS is confirmed.
- Host networking means Pi-hole owns 53, 80, and 443 on **every** interface — plan around
  that before adding other web services to this host.

## 4. Migrating config from the existing instance

Check versions first — v5 and v6 store configuration differently and Teleporter archives are
not cleanly cross-version:

```bash
pihole -v    # on .249
```

**v6 → v6 (used here):**

```bash
# on .249
pihole-FTL --teleporter                 # writes pi-hole_backup.zip

# on the XU4
docker cp pi-hole_backup.zip pihole:/tmp/
docker exec pihole pihole-FTL --teleporter /tmp/pi-hole_backup.zip
docker restart pihole
```

**v5 source — pull the pieces manually instead:**

```bash
grep PIHOLE_DNS /etc/pihole/setupVars.conf      # upstreams
cat /etc/pihole/custom.list                     # local A records
cat /etc/dnsmasq.d/05-pihole-custom-cname.conf  # CNAMEs
```

…then declare them as `FTLCONF_dns_hosts` / `FTLCONF_dns_cnameRecords` (semicolon-separated).

### ⚠️ Gotcha: `FTLCONF_*` env vars override imported config on every start

Pi-hole v6 re-applies `FTLCONF_*` environment variables at **each container start**,
overwriting matching values in `pihole.toml`. An imported upstream list will silently lose to
`FTLCONF_dns_upstreams`, making the import look like it failed.

Resolution used here: **env vars own DNS behavior** (upstreams, listening mode, web port) —
declarative, version-controlled, survives a container rebuild — while **Teleporter owns
gravity data** (adlists, local records, groups). No overlap, no surprises.

## 5. Verification

From another host on the LAN:

```bash
dig @10.x.x.250 google.com        # basic resolution
dig @10.x.x.250 swearengen        # local record survived the import
dig @10.x.x.250 doubleclick.net   # expect 0.0.0.0 — proves gravity imported
```

On the XU4:

```bash
docker exec pihole pihole status
docker exec pihole pihole-FTL --config dns.upstreams   # confirm Quad9, not imported values
docker logs pihole --tail 30                           # no bind errors
```

Web UI at `https://10.x.x.250/admin` — confirm Local DNS Records are populated and Adlists
show a nonzero gravity count. If gravity is 0, the list URLs imported but the blocklist data
did not: `docker exec pihole pihole -g`.

## 6. ⚠️ Redundancy is only real if clients know about both

Adding the second resolver to OPNsense DHCP covers dynamic clients. **Statically configured
hosts do not get that list** — during a `.249` outage, queries from static hosts still failed
because most had a single nameserver configured by hand at build time.

Audit target list (in rough order of how often they get missed):

- Proxmox hosts (`/etc/resolv.conf` or the DNS field in the web UI)
- VMs and LXCs given static addressing at build time
- TrueNAS SCALE (Network → Global Configuration)
- OPNsense itself (System → Settings → General → DNS servers)
- Appliances with independent config: printers, IPMI/BMC, switches, APs, IoT devices

Two details worth getting right:

- On `systemd-resolved` hosts, `/etc/resolv.conf` is often a symlink and hand edits are
  reverted on reboot — the real config lives in netplan or NetworkManager. `resolvectl status`
  shows what is actually in effect.
- **Resolver order matters.** Most stacks try the first server and fall back only after a
  timeout (~5s), so a dead primary yields slow-but-working resolution rather than clean
  failover. Varying which resolver is listed first across hosts avoids the whole fleet
  limping in unison.

> This audit is the motivating case for the planned Ansible `common` role: resolver
> configuration managed fleet-wide means the next DNS renumber is a variable change and a
> playbook run, not another manual sweep.

## Final state

```
                  ┌────────────────────────────────────┐
   DHCP clients ──┤ DNS1 10.x.x.250  (XU4, Docker Compose, this runbook)
   + static hosts ┤ DNS2 10.x.x.249  (LXC on swearengen, script install)
                  └────────────────────────────────────┘
                                │
                                └──> upstream: Quad9 9.9.9.9 / 149.112.112.112
```

Either resolver can be taken down for updates or reboots without a DNS outage — provided
every client actually lists both.
