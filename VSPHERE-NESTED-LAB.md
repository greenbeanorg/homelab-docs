# Nested vSphere 8 Lab — ESXi + vCenter on Proxmox

**Status:** IN PROGRESS — first `esxi01` install attempt done; full build planned for 2026-10-18
**Started:** 2026-10-16
**Hosts:** `esxi01` (VM 5000, swearengen), `esxi02` (planned, garrett), VCSA Tiny (planned, on `esxi01`), NFS datastore from TrueNAS
**Scope:** Refreshing hands-on vSphere skills from an ESXi + Windows-vCenter era (2013–2015) to vSphere 8: install, VCSA, cluster, distributed switch, NFS datastore, vMotion / storage vMotion, HA, templates, vLCM, scripting.
**Licensing:** Evaluation mode only — no borrowed keys.

---

## 1. Why nested, and what it costs

Running ESXi as a Proxmox guest means ESXi sees QEMU's virtual hardware,
not a real server, and ESXi only ships drivers for a short list of devices.
Most of the setup work is choosing virtual hardware ESXi has a driver for.

| Proxmox setting | Use | Why |
|---|---|---|
| Disk bus | **SATA** | ESXi's built-in `vmw_ahci` handles it. ESXi has no VirtIO drivers at all, and QEMU's PVSCSI emulation isn't detected by the ESXi 8 installer (§3.1). |
| Machine | **q35** | A real PCIe topology with an ICH9 AHCI controller, instead of i440FX's 1996-era flat PCI bus. ESXi 8 targets modern servers. |
| BIOS | **OVMF (UEFI)** + EFI disk | Broadcom is moving away from legacy BIOS boot. Choose firmware before installing — changing it later means reinstalling. |
| CPU | `host`, or a shared custom model with `+vmx` (§4.2) | ESXi needs hardware virtualization passed through to run 64-bit guests. |
| NIC | **vmxnet3** | ESXi has no VirtIO NIC driver; the e1000 driver is deprecated. |
| Host | `nested=Y` | `cat /sys/module/kvm_intel/parameters/nested` |

---

## 2. Status

| Item | State |
|---|---|
| `esxi01` VM created (i440FX, SeaBIOS, 4 vCPU `host`, 16 GB, 256 GB disk, vmxnet3 on VLAN 60) | ✅ |
| ESXi 8.0.2 installer boots | ✅ |
| Installer finds no disk on PVSCSI → disk moved to SATA | ✅ diagnosed — confirm install completed |
| Rebuild `esxi01` as q35 + UEFI + SATA, 20–24 GB | ⬜ Planned |
| `esxi02` on garrett | ⬜ Planned |
| VCSA Tiny deploy, datacenter + cluster | ⬜ Planned |
| vDS + VLAN port groups, NFS datastore, Linux VM | ⬜ Planned |
| vMotion, storage vMotion, HA failover test, templates/clones | ⬜ Planned |
| vLCM images; govc or PowerCLI scripting | ⬜ Planned |

---

## 3. Build log

### 3.1 First install attempt — "Select a Disk" shows nothing

**Symptom:** The ESXi 8.0.2 installer's disk selection screen listed
`Local: (none)` and `Remote: (none)`.

**Cause:** The 256 GB disk was on `scsi0` behind the **VMware PVSCSI**
controller. That looks like the obvious choice, since ESXi does ship a
pvscsi driver, but QEMU's emulation of the controller isn't detected by the
ESXi 7/8 installer. None of Proxmox's other SCSI controller types help
either: the LSI 53C8xx models and the LSI 1078-based MegaRAID have no ESXi 8
driver, and ESXi has never had VirtIO drivers. ESXi 7 also removed the
legacy vmklinux drivers that used to cover old controllers.

**Fix:** Move the disk off the SCSI controller onto SATA. SATA isn't a SCSI
controller type, so it isn't in the SCSI Controller dropdown; it's set on
the disk.

```bash
qm shutdown 5000
qm set 5000 --delete scsi0                          # disk becomes unused0
qm set 5000 --sata0 local-lvm:vm-5000-disk-0
qm set 5000 --boot order='ide2;sata0'               # ISO first for the install
qm config 5000
```

GUI equivalent: Hardware → Hard Disk → Disk Action → Detach → double-click
**Unused Disk 0** → Bus/Device **SATA 0** → Add, then Options → Boot Order.

The SCSI Controller setting can be left alone; with nothing attached, it
doesn't matter.

---

## 4. Plan for the full build

### 4.1 Sizing

| VM | Host | vCPU | RAM | Notes |
|---|---|---|---|---|
| `esxi01` | swearengen | 4 | 20–24 GB | Holds VCSA Tiny |
| VCSA Tiny | on `esxi01` | 2 | 14 GB | Thin disk mode: ~580 GB allocated, ~30–50 GB used at first |
| `esxi02` | garrett | 4 | 10–12 GB | k3s VMs paused for the session |

At 20 GB, ESXi takes ~3 GB and VCSA 14 GB, leaving ~3 GB for test guests.
24 GB is more comfortable if swearengen has the headroom.

### 4.2 CPU compatibility for vMotion

swearengen is a Comet Lake i5-10600K; garrett is a Kaby Lake i7-7700K. With
`cpu: host` on both, the ESXi guests see different CPUID sets and vMotion
between them fails. EVC on nested ESXi is unreliable.

**Plan:** Give both ESXi VMs the same Proxmox custom CPU model — a
Skylake-Client base (both chips are Skylake-family) plus `+vmx` — defined
in `/etc/pve/virtual-guest/cpu-models.conf`. Both nested hosts then present
an identical CPU. ESXi 8 may warn that Skylake is deprecated; it still
installs.

### 4.3 Networking

- **Proxmox firewall MAC filter.** Nested guests (VCSA and test VMs) send
  frames with their own MACs through the ESXi VM's NIC. The Proxmox VM
  firewall's **MAC filter** drops those. Either set `firewall=0` on the
  ESXi VMs' NICs or disable MAC filter under the VM's Firewall → Options.
  Remember `qm set --net0` replaces the whole parameter.
- **No double-tagging.** Proxmox tags VLAN 60 on the ESXi VM's NIC, so the
  ESXi-side port group for that NIC stays at VLAN 0 (untagged). For a vDS
  carrying several VLANs, trunk the ESXi VM's NIC instead (no Proxmox tag)
  and tag in the port groups.
- **Both hosts carry the VLANs.** garrett's switch port must trunk the lab
  VLANs for the vDS and vMotion to work across hosts. The sunroom TL-SG108E
  only tags lab VLANs on ports 1–2 — if garrett hangs off it, add the VLANs
  there too (the Kea lab lost VLAN 61 at exactly this switch; see
  KEA-DHCP-LAB.md §8.1).
- **Storage path.** NFS from TrueNAS on swearengen to `esxi02` crosses
  1 GbE. Keep storage-vMotion test VMs small.

### 4.4 vCenter prerequisites

- **Forward and reverse DNS first.** Pick a static IP and FQDN for the
  appliance and create the Pi-hole A record. Confirm `nslookup <ip>`
  returns the FQDN. A failed reverse lookup is the most common reason a
  VCSA install fails.
- **Time.** NTP configured, clocks agreeing.
- **Installer runs from the desktop.** Mount the VCSA ISO on Windows and run
  `vcsa-ui-installer\win32\installer.exe`; it targets `esxi01` over the
  network. Stage 1 deploys the appliance; Stage 2 configures SSO and NTP.
  If Stage 1 succeeds and Stage 2 hangs, check DNS and time first.

### 4.5 Licensing and lifecycle caveats

- **Evaluation mode is 60 days.** Broadcom ended free ESXi.
- **vLCM may be blocked.** Image-based vLCM needs a depot. Broadcom's online
  depot now requires a download token tied to an entitlement. Without one,
  vLCM may be limited to an imported offline bundle, or covered in the UI
  and conceptually only.

---

## Quick reference

| | |
|---|---|
| Nested virt on? | `cat /sys/module/kvm_intel/parameters/nested` → `Y` |
| VM config | `qm config 5000` |
| Disk to SATA | `qm set 5000 --delete scsi0` → `qm set 5000 --sata0 <storage>:<volume>` |
| Machine type | `qm set 5000 --machine q35` |
| VCSA Tiny | 2 vCPU / 14 GB RAM; choose Thin Disk Mode |
| VCSA must-haves | Static IP, forward **and** reverse DNS, NTP |
| Nested guest traffic dropped? | Proxmox VM firewall → MAC filter |
