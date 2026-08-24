# PIHOLE-DOCKER — Primary Resolver Build Procedure

Container build for the **primary** Pi-hole on the ODROID-XU4. This is the procedure
[DNS.md §7](DNS.md#7-rebuild-from-nothing) step 4 refers to when it says "Armbian image →
Docker → compose file with the pinned tag → Teleporter import."

Scoped deliberately narrow. `DNS.md` owns the design and the running system; this document
owns **getting the container to exist on a freshly flashed board**, which is the part
`DNS.md` assumes is already done.

- **Host:** `pihole` — ODROID-XU4, Armbian, `10.x.x.250`
- **Compose root:** `/opt/pihole`
- **Prerequisite:** [ODROID-XU4.md](ODROID-XU4.md) — a maintained OS on the board
- **Date:** August 2026

| Question | Document |
| --- | --- |
| Why two resolvers, failure domains, DHCP option 6 | [DNS.md §1–2](DNS.md) |
| Running config, tag pinning rationale, routine update | [DNS.md §3](DNS.md) |
| Blocklists, parity, `pihole -q --partial` | [DNS.md §5](DNS.md) |
| Known limitations, full-outage restore order | [DNS.md §6–7](DNS.md) |
| **First-build procedure on bare Armbian** | **this document** |

---

## 1. ⚠️ Free port 53 from systemd-resolved

A fresh Armbian install runs a `systemd-resolved` stub listener, and `/etc/resolv.conf`
points at it:

```
# ss -lntup | grep ':53'
udp UNCONN 0 0    127.0.0.54:53 0.0.0.0:* users:(("systemd-resolve",...))
udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",...))
tcp LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",...))
tcp LISTEN 0 4096    127.0.0.54:53 0.0.0.0:* users:(("systemd-resolve",...))
```

The stub binds `127.0.0.53`/`127.0.0.54`, not `0.0.0.0`, so a host-networked container
*can* take `:53` on the LAN address without an immediate conflict. Don't rely on that. The
real problem is `resolv.conf` pointing at a stub that loops back into the container once
Pi-hole is the local resolver.

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
own container, a failed container start leaves a box that cannot resolve anything —
including the registry lookup needed to pull a working image. That is recoverable only if
you remember it while it's happening.

---

## 2. Static addressing

Host networking binds host interfaces, so `10.x.x.250` must belong to the host **before**
the container starts. There is no `-p 10.x.x.250:53:53` equivalent in host mode.

Set it via `nmtui` or the Armbian NetworkManager config and confirm with `ip addr`. The
address sits above the Kea pool by design — see [DNS.md §2](DNS.md).

⚠️ The NIC is a **USB ethernet adapter** (`enx…`), not onboard. Interface names are
MAC-derived, so replacing the adapter renames the interface and orphans the static config.

---

## 3. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version
```

⚠️ **armhf image availability.** The XU4 is ARMv7/armhf. Pi-hole publishes `linux/arm/v7`,
so this works — but a growing number of projects ship arm64/amd64 only. A
`no matching manifest` error on this board means 32-bit ARM was dropped upstream, and the
answer is a different project or a different host, not a workaround.

---

## 4. Compose file

`/opt/pihole/.env`:

```
PIHOLE_TAG=2026.07.2
```

`/opt/pihole/docker-compose.yml`:

```yaml
services:
  pihole:
    container_name: pihole
    image: pihole/pihole:${PIHOLE_TAG}
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

Two things about that file that aren't obvious:

- **`443s`** — the trailing `s` is civetweb syntax for a TLS port, not a typo. Listing only
  `443s` disables plain HTTP entirely; Pi-hole generates a self-signed cert at
  `/etc/pihole/tls.pem` on first start. Use `'80,443s'` during initial setup if there's any
  risk of locking yourself out of a headless box, then drop `80` once HTTPS is confirmed.
- **Host networking means Pi-hole owns 53, 80 and 443 on every interface.** Plan around
  that before adding any other web service to this board.

Why the tag is pinned rather than `latest`, and how routine updates run afterwards:
[DNS.md §3](DNS.md#3-primary-pihole-on-the-odroid-xu4).

---

## 5. First-time config import

[DNS.md §5](DNS.md#5-keeping-the-two-in-parity) covers ongoing parity between the two
resolvers via the web UI. This section covers the **initial** import onto an empty
container, which is a CLI operation and has a version trap in front of it.

Check versions first — v5 and v6 store configuration differently and Teleporter archives do
not cross that boundary cleanly:

```bash
pihole -v    # on the source instance
```

**v6 → v6:**

```bash
# on the source
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

…then declare them as `FTLCONF_dns_hosts` / `FTLCONF_dns_cnameRecords`, semicolon-separated.

### Division of ownership

The `FTLCONF_*` override behaviour ([DNS.md §3](DNS.md)) makes an import look like it
failed when it didn't — the imported value is there, then gets overwritten on the next
start. The split settled on here:

- **Env vars own DNS behaviour** — upstreams, listening mode, web port. Declarative,
  version-controlled, survives a container rebuild.
- **Teleporter owns gravity data** — adlists, local records, CNAMEs, groups.

Nothing appears in both. When something does, the env var wins silently.

### Import verification

```bash
docker exec pihole pihole-FTL --config dns.upstreams   # Quad9, not the imported values
docker compose logs --tail=50                          # no bind errors
```

Web UI at `https://10.x.x.250/admin` — Local DNS Records populated, Adlists showing a
nonzero gravity count. If gravity is 0 the list URLs imported but the blocklist data did
not: `docker exec pihole pihole -g`.

Resolver smoke tests are in the [DNS.md](DNS.md) quick reference.

---

## 6. Not yet implemented

- **`diun` update notifications.** Label-driven image-update watcher, evaluated but not
  deployed. Would need `watch_repo=true`, `sort_tags=lexicographical`, an `include_tags`
  regex matching Pi-hole's date-based format (`^\d{4}\.\d{2}(\.\d+)?$`), and
  `diun.platform=linux/arm/v7` for this board. Without it, a pinned tag means updates
  happen only when someone remembers to look.
