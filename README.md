# homelab-docs

Production-style runbooks for a multi-site homelab: three Proxmox hosts across
two sites, ZFS storage on TrueNAS SCALE, OPNsense edge routing over XGS-PON
fiber, verified off-site backups, and a containerized service stack.

These are working documents, not tutorials. Each one records what was actually
built, why the approach was chosen, what broke along the way, and how to rebuild
it from nothing.

---

## Runbooks

### System Automation
| Doc | Covers |
| --- | --- |
| [TERRAFORM-PROXMOX-FIRST-APPLY-2026-09.md](./TERRAFORM-PROXMOX-FIRST-APPLY-2026-09.md) | First Terraform + Proxmox LXC provisioning pass from dority; covers cert/hostname drift and API token permission scoping gotchas |

### System Troubleshooting
| Doc | Covers |
| --- | --- |
| [SWEARENGEN-VMBR0-INTRA-BRIDGE-FORWARDING-BUG-2026-09.md](SWEARENGEN-VMBR0-INTRA-BRIDGE-FORWARDING-BUG-2026-09.md) | Workaround in place, root cause unresolved. Same-host VM-to-VM TCP flow silently dropped by vmbr0's intra-bridge forwarding for one specific VM pair, despite clean firewall/VLAN/FDB/physical-network state at every layer checked. Fixed by routing the flow through OPNsense instead of the local bridge. |
| [SWEARENGEN-USB-CONTROLLER-HANG-2026-09.md](SWEARENGEN-USB-CONTROLLER-HANG-2026-09.md) | **Resolved.** Root-cause runbook for a full hypervisor hang on swearengen (i5-10600K/48GB, primary Proxmox host) traced to a wedged USB controller shared between the UPS and other USB peripherals. Fix was moving the UPS off swearengen entirely — see UPS.md.

### Monitoring
| Doc | Covers |
| --- | --- |
| [UPTIME-KUMA.md](UPTIME-KUMA.md) | Declarative availability monitoring — monitors defined in YAML and reconciled into Uptime Kuma by a Python script, so the monitor set is version-controlled rather than click-configured |

### Networking
| Doc | Covers |
| --- | --- |
| [VLAN-SEGMENTATION.md](VLAN-SEGMENTATION.md) | Migration of greenbean.org from a flat 10.x.x.N/24 to a segmented VLAN network on 10.79.x.x. Covers the CRS310, a new TL-SG108E access switch, OPNsense, both Proxmox hosts, and the EAP610 AP.
| [DNS.md](DNS.md) | Redundant Pi-hole resolvers in separate failure domains, advertised by Kea DHCPv4 option 6 — address-space layout, Teleporter parity, the static-host audit procedure, and the failure modes DHCP resolver lists actually have |
| [ODROID-XU4.md](ODROID-XU4.md) | Rebuilding the DNS host off an orphaned Hardkernel vendor kernel onto maintained Armbian — why a release upgrade was rejected, U-Boot's fixed-filename boot flow, and the eMMC reflash with a rollback path |
| [PIHOLE-DOCKER.md](PIHOLE-DOCKER.md) | First-build procedure for the primary Pi-hole container on bare Armbian — freeing :53 from systemd-resolved, armhf image constraints, compose layout, and the initial Teleporter import |
| [UNBOUND.md](UNBOUND.md) | Per-host recursive, DNSSEC-validating resolver replacing Quad9 as upstream on both Pi-holes — install, validation, cutover, and rollback |
| [WIREGUARD.md](WIREGUARD.md) | Multi-site overlay network — hub-and-spoke WireGuard through a cloud instance so neither residential endpoint needs inbound reachability, with subnet routing to a second site and full-tunnel roaming clients |

### Storage
| Doc | Covers |
| --- | --- |
| [TRUENAS.md](TRUENAS.md) | 30 TB storage migration — mdadm RAID5 → TrueNAS SCALE / ZFS RAIDZ1, with PCIe SATA controller passthrough, pool and dataset design, and dual SMB/NFS shares under a unified identity |
| [SMART-DOCTOR.md](SMART-DOCTOR.md) | Using smartmontools to establish a base health for the 4 x 10TB NAS drives.
| [TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md) | Why the TrueNAS reporting page stays blank when NUT runs in netclient mode (NAS-132924) — the charts.d module that assumes a local `upsd`, a config override that fixes it without touching the immutable rootfs, and an init script to survive OS upgrades |
| [UPS.md](UPS.md) | UPS monitoring via NUT, primary relocated to a dedicated Pi 2B with staggered shutdown ordering across three hosts (TrueNAS first, swearengen last) |

### Experimental / early-stage
| Doc | Covers |
| --- | --- |
| [NETBOX.md](NETBOX.md) | NetBox, PostgreSQL, and Valkey through Docker Compose.
| [NETBOX-INVENTORY.md](NETBOX-INVENTORY.md) | Netbox python inventory script fed by a simple yaml
| [LAN-DEVICE-WATCHER.md](LAN-DEVICE-WATCHER.md) | A crude Node.js LAN scanner

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
