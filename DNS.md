# DNS — Redundant Pi-hole Resolvers

Network-wide DNS and ad/tracker filtering for the lab, served by **two
independent Pi-hole v6 instances** in deliberately different failure domains and
advertised to clients by **Kea DHCPv4** on the OPNsense edge router.

The goal is not load balancing. The goal is that patching, rebooting, or losing
either resolver — including the Proxmox host one of them lives on — does not
take the household off the internet.

- **Primary:** `pihole` — ODROID-XU4, Armbian, Pi-hole in Docker — **10.x.x.250**
- **Secondary:** `pihole2` — LXC 1000 on `wu`, Pi-hole host install — **10.x.x.249**
- **DHCP:** Kea DHCPv4 on OPNsense — subnet `10.x.x.0/24`, pool `10.x.x.201-239`, option 6
- **Local zone:** ~20 hand-maintained A records under `greenbean.org`
- **Upstreams:** Quad9
- **Date:** August 2026

---

## 1. Design

[#1-design](#1-design)

```
                    ┌─────────────────────────────┐
  LAN client ──DHCP─┤ OPNsense (VM on wu)         │
                    │ Kea DHCPv4                  │
                    │  option 6 → .250, .249      │
                    └─────────────────────────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              ▼                                     ▼
   pihole  10.x.x.250                    pihole2  10.x.x.249
   ODROID-XU4 (bare hardware)            LXC 1000 on wu
   Pi-hole in Docker                     Pi-hole host install
              └──────────────┬──────────────────────┘
                             ▼
                        Quad9 upstream
```

Two decisions worth stating explicitly, because both are the kind of thing that
looks arbitrary later:

**Separate hardware, not two guests on one box.** The primary lives on a
standalone ODROID-XU4 that shares no PSU, no hypervisor, and no kernel with the
rest of the lab; the secondary is a guest on `wu`. `swearengen` — the busiest
host, and the one that actually gets rebooted for PVE updates — carries neither
resolver, so routine maintenance on it never touches DNS.

**The secondary sharing a host with the router is deliberate, not an
oversight.** `wu` runs the OPNsense VM, so a `wu` outage takes routing and the
secondary resolver together. That costs almost nothing: with routing down there
is no upstream to resolve against anyway, and the primary on the XU4 keeps
answering local `greenbean.org` records so the NAS, Plex, and Home Assistant
stay reachable by name while the internet is out. The pairing that actually
matters — both resolvers dying at once — requires two unrelated hosts to fail.

**Different install methods, not by choice.** The secondary predates the
primary — it was the original `curl | bash` host install, and the XU4 Docker
instance was seeded from its Teleporter export. Keeping both is a *liability*
(two upgrade paths, two ways for config to drift), not a feature. See
[§6](#6-known-limitations).

**The router is not a resolver.** OPNsense forwards nothing and runs no local
DNS role in this design — it only *advertises* the two Pi-holes. Enabling
Unbound on OPNsense and pointing clients at the firewall would collapse both
resolvers into one failure domain and is explicitly avoided.

---

## 2. DHCP handoff (Kea DHCPv4)

[#2-dhcp-handoff-kea-dhcpv4](#2-dhcp-handoff-kea-dhcpv4)

OPNsense → **Services → Kea DHCP → [DHCPv4] → Subnets**:

| Field | Value |
| --- | --- |
| Subnet | `10.x.x.0/24` |
| Pools | `10.x.x.201 - 10.x.x.239` |

Then, on that subnet, → **Options**:

| Field | Value |
| --- | --- |
| Description | `DNS_1` |
| Set Code | `domain server [6]` |
| Set Data | `10.x.x.250, 10.x.x.249` |

**The pool deliberately stops well short of the resolvers.** Dynamic leases
occupy `.201–.239`; both Pi-holes sit at `.249` and `.250`, in static space
above the pool, alongside the rest of the fixed infrastructure. Nothing Kea
hands out can ever collide with a resolver address. Keep it that way — if the
pool is ever widened, widen it *downward*, not up.

That leaves **39 dynamic leases**. Fine today, but it is a low ceiling for a
flat /24 with guests, phones, and IoT on it; if lease exhaustion ever shows up
as "some devices can't get on the wifi," this is the first place to look.

Both addresses go in **one** option-6 entry, comma-separated, in preference
order. Do not create a second option-6 row — a repeated code is not an
appended list, and behaviour when Kea sees a duplicate is not something to
rely on.

Kea's runtime config is **generated** from OPNsense's `config.xml` into
`/usr/local/etc/kea/kea-dhcp4.conf`. Never hand-edit that file; the next config
save overwrites it. Read it to confirm what was rendered:

```bash
# on OPNsense
grep -A3 domain-name-servers /usr/local/etc/kea/kea-dhcp4.conf
```

Leases are visible under **Services → Kea DHCP → Leases** in the UI, and on
disk as a CSV under `/var/db/kea/`.

Clients pick up the change on **lease renewal**, not immediately. Force it or
wait out the lease:

```bash
# Linux (systemd-networkd / NetworkManager)
sudo dhclient -r && sudo dhclient        # or: nmcli con up <name>
resolvectl status | grep -A2 'DNS Servers'

# Windows
ipconfig /release && ipconfig /renew && ipconfig /all | findstr /i "DNS Servers"
```

---

## 3. Primary — `pihole` on the ODROID-XU4

[#3-primary-pihole-on-the-odroid-xu4](#3-primary-pihole-on-the-odroid-xu4)

```
 v26.8.1 for Odroid XU4 running Armbian Linux 6.6.143-current-odroidxu4
 Packages:     Ubuntu stable (resolute)
 Containers:   pihole
 Memory usage: 11% of 1.94G      Usage of /: 25% of 7.0G
```

The board previously ran Ubuntu 22.04 with a **Hardkernel-provided kernel that
had been orphaned upstream** — no security updates, no path forward. It was
rebuilt on Armbian, which keeps the XU4 (armv7, Exynos5422) on a maintained
`6.6.x` kernel with an Ubuntu userland on top. That migration is its own
document: [ODROID-XU4.md](ODROID-XU4.md).

Pi-hole runs as a single container, **not** as a host install:

- `host` networking — Pi-hole binds :53 directly; no port-mapping games
- Image tag **pinned** (Pi-hole ships date-based tags like `2026.07.2`, not
  semver) via `PIHOLE_TAG=` in a `.env` beside the compose file. `latest` on the
  network's primary resolver is an unscheduled outage waiting for a bad push.
- Web UI HTTPS-only on `443`, self-signed cert generated at first start
- Upstreams: Quad9

```bash
aba@odroidxu4:~$ docker ps -a
CONTAINER ID   IMAGE                  COMMAND      STATUS                NAMES
abefc3caa26f   pihole/pihole:latest   "start.sh"   Up 2 days (healthy)   pihole
```

> **Gotcha — `FTLCONF_*` wins, silently.** Any `FTLCONF_*` environment variable
> in the compose file is re-applied on **every container start** and overrides
> whatever is in the imported config or set in the web UI. A setting changed in
> the UI will appear to work and then silently revert on the next `docker
> compose up`. If a setting must persist, either set it in the environment or
> keep it out of the environment entirely — never both.

**Routine update** (this is the primary resolver — see
[§6](#6-known-limitations) before running it unattended):

```bash
sudo apt update && sudo apt -y dist-upgrade && sudo apt -y autoremove
cd ~/pihole && docker compose pull && docker compose up -d
sleep 15 && docker compose logs --tail=30
dig +short @127.0.0.1 example.com                 # smoke test
[ -f /var/run/reboot-required ] && echo "REBOOT REQUIRED"
```

---

## 4. Secondary — `pihole2` LXC

[#4-secondary-pihole2-lxc](#4-secondary-pihole2-lxc)

Ubuntu 24.04 LXC (VMID **1000**) on `wu`, Pi-hole installed by the official
script (`curl -sSL https://install.pi-hole.net | bash`) directly on the
container OS.

```bash
root@wu:~# pct list
VMID       Status     Lock         Name
1000       running                 pihole2
```

- Same three blocklists, same upstreams, same local A records as the primary
- Updated with `pihole -up` plus normal `apt` maintenance — **a different
  upgrade path from the primary**, which is the drift risk called out in
  [§6](#6-known-limitations)
- Unprivileged LXC; if this container is ever rebuilt, confirm it still binds
  :53 on both UDP and TCP before declaring it healthy

---

## 5. Keeping the two in parity

[#5-keeping-the-two-in-parity](#5-keeping-the-two-in-parity)

The primary is the source of truth. Parity is maintained by **Teleporter**
export/import, not by hand-editing both:

1. Primary web UI → **Settings → Teleporter → Export** (downloads a `.zip`)
2. Secondary web UI → **Settings → Teleporter → Import**, select the archive
3. Re-run gravity on the secondary and compare counts against the primary

Blocklists currently in use on both (HaGeZi):

| List | Purpose |
| --- | --- |
| Multi PRO | Main ads/tracking/telemetry list |
| TIF (medium) | Threat intelligence feeds — malware, phishing |
| spam-tlds-adblock | Blanket block on abuse-heavy TLDs |

Combined gravity: **~593,000 domains**, 0 invalid, all lists status `1` on both
hosts. If the two counts diverge, the import didn't take — re-run it rather
than adding lists by hand on one side.

> **Gotcha — `pihole -q` lies about HaGeZi.** HaGeZi ships ABP-style rules
> (`||domain^`), and `pihole -q` matches **exactly** by default, so a blocked
> domain will come back "not found". Always use `--partial`:
>
> ```bash
> pihole -q --partial doubleclick.net     # finds it
> pihole -q doubleclick.net               # false negative
> ```
>
> Confirm with an actual query instead of trusting the lookup:
> `dig +short @10.x.x.250 stats.g.doubleclick.net` → `0.0.0.0`

---

## 6. Known limitations

[#6-known-limitations](#6-known-limitations)

**Option 6 is not failover.** DHCP hands clients an ordered *list*; what
happens when the first entry stops answering is entirely up to the client's
stub resolver. Windows, systemd-resolved, Android, and embedded devices all
behave differently — some retry the second server in under a second, some hang
for the full timeout on every query, some cache the first server until the
interface bounces. Expect a degraded-but-working experience during a primary
outage, not a seamless one.

**Static hosts bypass DHCP entirely.** Proxmox nodes, TrueNAS, the CRS310, the
Mac, and anything else with a hand-configured address never see option 6. An
outage on `.249` previously took down hosts that had been configured with a
single nameserver. **A full static-host DNS audit — confirming every manually
configured host lists both resolvers — is outstanding.**

**Two install methods, two upgrade paths.** Docker on the primary, host install
on the secondary. Nothing enforces version parity; they can drift a release
apart without anything alerting. Converging the secondary onto the same Docker
pattern is the standing fix, not yet done.

**Never update both at once.** A `dist-upgrade` on the XU4 with a kernel change
means a reboot of the network's primary resolver. Update one, verify the other
is answering, then update the second.

**The local zone is hand-maintained.** ~20 A records under `greenbean.org`
entered through the UI. Adding a host means remembering to add it to both
Pi-holes (or re-running Teleporter). This is the weakest link in the design and
the strongest argument for moving the zone into version control.

**Monitoring covers up/down, not correctness.** Uptime Kuma watches both hosts
(see [UPTIME-KUMA.md](UPTIME-KUMA.md)), but a Pi-hole that is up and returning
NXDOMAIN for everything looks healthy. A DNS-query-type monitor against a known
record on each resolver is the right check here.

---

## 7. Rebuild from nothing

[#7-rebuild-from-nothing](#7-rebuild-from-nothing)

If both resolvers are gone, DNS for the whole LAN is gone with them. Restore
order:

1. **Unbreak the network first.** On OPNsense, temporarily set option 6 to
   `9.9.9.9` so clients resolve while you work. The lab loses filtering and
   local `greenbean.org` names — that's acceptable for an hour.
2. **Rebuild the secondary** (fastest path — an LXC from template, then the
   install script). Import the most recent Teleporter archive.
3. **Point option 6 at the secondary only**, renew a client, confirm resolution
   and local records.
4. **Rebuild the primary** (Armbian image → Docker → compose file with the
   pinned tag → Teleporter import).
5. **Restore option 6 to `.250, .249`** and verify both independently before
   walking away.

Keep a current Teleporter export off-box — it is in the restic set, but a copy
you can reach without working DNS is worth more.

---

## Quick reference

| | |
| --- | --- |
| Primary resolver | `pihole` / `odroidxu4` — `10.x.x.250`, Docker container `pihole` |
| Secondary resolver | `pihole2` — `10.x.x.249`, LXC 1000 on `wu` (`pct enter 1000`) |
| DHCP subnet / pool | `10.x.x.0/24`, pool `10.x.x.201 - 10.x.x.239` (39 dynamic leases) |
| DHCP option | OPNsense → Kea DHCP → DHCPv4 → Subnets → Options → code **6**, data `10.x.x.250, 10.x.x.249` |
| Rendered Kea config | `/usr/local/etc/kea/kea-dhcp4.conf` (generated — do not edit) |
| Test a resolver | `dig +short @10.x.x.250 example.com` |
| Test blocking | `dig +short @10.x.x.250 stats.g.doubleclick.net` → `0.0.0.0` |
| Search blocklists | `pihole -q --partial <domain>` (`--partial` is mandatory for HaGeZi) |
| Primary container logs | `cd ~/pihole && docker compose logs --tail=50` |
| Secondary version/update | `pihole -v` / `pihole -up` |
| Rebuild gravity | `pihole -g` (or `docker exec pihole pihole -g` on the primary) |
| Config sync | Web UI → Settings → Teleporter (export primary → import secondary) |
| Client renew (Linux) | `sudo dhclient -r && sudo dhclient` |
| Client renew (Windows) | `ipconfig /release && ipconfig /renew` |
| Emergency upstream | Set option 6 to `9.9.9.9` on OPNsense |
