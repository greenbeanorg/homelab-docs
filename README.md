# homelab-docs

Runbooks and design documents for a multi-site homelab, written the way I'd write them for a team: problem → design decisions → gotchas actually hit → resolution → verification.

> **Why runbooks?** Anyone can follow a tutorial. These docs capture the parts tutorials skip — the permission fight, the startup race, the flag that silently gets eaten — because that's where the actual sysadmin work lives.

## Index

| Area | Doc | Status |
|---|---|---|
| Storage | [TrueNAS SCALE migration: mdadm RAID5 → ZFS RAIDZ1](TRUENAS.md) | ✅ Complete |
| Network | Edge routing: OPNsense + XGS-PON ONT-on-a-stick (8311) | 🚧 Draft |
| Network | [WireGuard hub-and-spoke VPN](WIREGUARD.md) | ✅ Complete |
| Network | VLAN segmentation redesign (CRS310) | 🚧 In progress |
| Power | [UPS monitoring: pwrstat → NUT conversion](UPS.md) | 🚧 1 of N hosts |
| Power | [TrueNAS SCALE: blank UPS graphs in netclient mode (NAS-132924)](TRUENAS-UPS-REPORTING.md) | ✅ Complete |
| Services | [Dockerized Pi-hole on ODROID-XU4 (redundant DNS)](PIHOLE-DOCKER.md) | ✅ Complete |
| Hosts | [ODROID-XU4: Ubuntu 24.04 → Armbian 26.04 migration](XU4-ARMBIAN.md) | ✅ Complete |
| Backup | restic: multi-site backup architecture | 🚧 Rebuild pending post-migration |
| Services | Media automation stack (Docker Compose) | 🚧 Draft |
| Tooling | workspace.sh: GNU Screen multi-host session manager | 🚧 Draft |
| Automation | Ansible fleet roles (baseline, NUT, restic, Docker hosts) | 📋 Planned |
| Automation | Terraform: declarative Proxmox VM provisioning | 📋 Planned |
| Monitoring | Prometheus + Grafana + node_exporter, NUT exporter for UPS metrics | 📋 Planned |

## Environment overview

| Host | Role | Platform |
|---|---|---|
| `swearengen` | Primary Proxmox host — TrueNAS SCALE VM (ZFS pool `tank`, 4-wide RAIDZ1, PCIe SATA passthrough, Docker service stack), `farnum` (Plex), and assorted guests | i5-10600K / 48GB |
| `wu` | Proxmox node — OPNsense edge router VM, `pihole2` LXC (secondary DNS) | ODROID-H3, Celeron N5105 / 31GB |
| — | Primary network-wide DNS — Pi-hole in Docker | ODROID-XU4 (Exynos 5422, armhf), Armbian / Ubuntu 26.04 |
| remote | Proxmox — Pi-hole for a friend's LAN, LinuxGSM game server | — |
| `kk1` | Oracle Cloud Always Free — WireGuard hub, off-site restic target | Ampere A1, 4 vCPU / 24GB, aarch64 |

Switching: MikroTik CRS310-8G+2S+ (RouterOS) — bridge-WAN carries the XGS-PON SFP+ and the OPNsense WAN port; bridge-LAN carries everything else.

Storage: 4 × WD100EMAZ (Ultrastar He10) in a single RAIDZ1 vdev. Baseline SMART at ~63.5K power-on hours: zero reallocated/pending sectors, helium level 100 across all four.

> **Sanitization note:** All IPs, hostports, keys, and secrets in these docs and companion configs are scrubbed or replaced with placeholders. Topology and design are real; identifiers are not.

## Conventions

- ALL-CAPS filenames at repo root; one runbook per file
- Commands shown were actually run; output is trimmed but not fabricated
- ⚠️ callouts mark the gotchas that cost real time
- Each runbook ends with a **Verification** section — if you can't verify it, you didn't finish it
