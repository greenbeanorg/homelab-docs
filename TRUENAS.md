# TrueNAS SCALE Migration — mdadm RAID5 → ZFS RAIDZ1

Migration of the bulk storage array from a Proxmox-hosted **mdadm RAID5** to
**TrueNAS SCALE 25.10.4 (Goldeye)** running as a VM on Proxmox, with the SATA
controller passed through so ZFS owns the raw disks.

- **Proxmox host:** `swearengen` — Proxmox VE 9.2.3, kernel 7.0.2-6-pve, EFI boot
- **TrueNAS VM:** `truenas` (VMID 1000), static IP **10.x.x.x**
- **NFS clients:** `ellsworth` (transmission-daemon), `farnum`
- **Date:** June 2026

---

## 1. Hardware

| Component | Detail |
|-----------|--------|
| CPU | Intel i5-10600K (6c/12t) |
| RAM | 48 GB DDR4 |
| Boot disk | Samsung 980 PRO 2 TB NVMe (Proxmox host — **not** on the SATA controller) |
| Data disks | 4 × WDC WD100EMAZ 10 TB (CMR / helium, HC510-class) |
| SATA controller | `00:17.0` Intel Comet Lake SATA AHCI `[8086:06d2]` |

The four WD drives are CMR (not SMR), so they resilver cleanly under ZFS. RAM is
non-ECC, which is fine — ECC is recommended for ZFS, not required.

---

## 2. Why a destroy-and-rebuild was required

ZFS cannot adopt an existing mdadm array — there is no in-place mdadm→RAIDZ
conversion. The moment ZFS takes the disks it writes its own labels and the
RAID5 is gone. So "migrating" meant **back up everything first**, then destroy
the array and build a fresh ZFS pool. The array was fully backed up before any
of the steps below.

Because the Proxmox boot disk is the NVMe (on its own PCIe lanes, not the SATA
controller), passing through `00:17.0` detaches only the four WD drives from the
host and leaves Proxmox booting normally.

---

## 3. SATA controller passthrough (on `swearengen`)

### 3.1 Pre-flight: unhook the array from the host

Once `vfio-pci` claims the controller, the WD disks vanish from the host. Any
leftover mount or auto-assembly will drop the host into emergency mode at boot,
so the array must be fully detached first.

```bash
sudo mdadm --stop /dev/md0

# Comment out any array entry so it can't auto-mount / auto-assemble
sudo nano /etc/fstab            # prefix any md0 / array-UUID line with #
sudo nano /etc/mdadm/mdadm.conf # comment the ARRAY line if present
sudo update-initramfs -u
```

### 3.2 Verify IOMMU (already enabled)

```bash
dmesg | grep -e DMAR -e IOMMU
```

Confirmed enabled — kernel 7.0.2-6-pve enables Intel IOMMU by default, so **no
kernel command-line change was needed**. The key lines:

```
DMAR: Intel(R) Virtualization Technology for Directed I/O
DMAR-IR: Enabled IRQ remapping in x2apic mode   # interrupt remapping = required
```

> Note: `proxmox-boot-tool status` reported no UUIDs, meaning this host boots via
> **GRUB**, not systemd-boot. If a kernel param is ever needed it goes in
> `/etc/default/grub` → `update-grub` (not proxmox-boot-tool).

### 3.3 Confirm the IOMMU group is isolated

```bash
for d in /sys/kernel/iommu_groups/*/devices/*; do
  n=${d#*/iommu_groups/}; n=${n%%/*}
  printf 'Group %s: ' "$n"; lspci -nns "${d##*/}"
done | sort -V | grep '00:17.0'
```

Result: `00:17.0` sits **alone in IOMMU group 5** — clean to pass through.

### 3.4 Bind the controller to vfio-pci

Early binding so `vfio-pci` claims the controller at boot before `ahci`:

```bash
sudo nano /etc/modprobe.d/vfio.conf
```

```
options vfio-pci ids=8086:06d2
softdep ahci pre: vfio-pci
```

```bash
sudo update-initramfs -u
sudo reboot
```

### 3.5 Verify the handoff

```bash
lspci -nnk -s 00:17.0    # want: Kernel driver in use: vfio-pci
lsblk                     # want: only nvme0n1 (the WD disks gone from host)
```

---

## 4. TrueNAS VM creation (VMID 1000)

Built with **"Start after created" unchecked**, controller attached, then
installed.

| Setting | Value | Why |
|---------|-------|-----|
| Machine | **q35** | Required for PCIe passthrough |
| BIOS | **OVMF (UEFI)** + EFI disk on NVMe | UEFI guest |
| Secure Boot | **Disabled** (EFI "Pre-Enroll keys" unchecked) | Installer wouldn't boot otherwise |
| SCSI controller | VirtIO SCSI single | |
| Boot disk | **64 GB**, SCSI, on `local-lvm` (NVMe) | OS disk — never on the array |
| CPU | type **host**, 4 cores | Full instruction set for ZFS |
| Memory | **16 GB, ballooning OFF** (`balloon=0`) | ZFS ARC and ballooning conflict |
| Network | VirtIO (paravirtualized), `vmbr0` | |
| PCI device | `hostpci0: 0000:00:17,pcie=1` | The passed-through SATA controller |

### Display fix — green/garbled installer screen

The installer rendered as green static under the default VGA + OVMF combo. Fix:
shut down → **Hardware → Display → Edit** → change to **VirtIO-GPU** (or **SPICE**,
which needs the virt-viewer client). The install was running fine underneath; it
was purely a noVNC rendering issue. The display type is set permanently — TrueNAS
is administered via its web UI, so it doesn't matter which adapter it lands on.

---

## 5. Install & initial config

1. Boot installer → **Install/Upgrade**.
2. **Install target: the 64 GB QEMU HARDDISK only** — never one of the
   `WD100EMAZ` drives (those become the pool).
3. Set admin password.
4. On completion, **shut down** (not reboot), then set the CD/DVD drive to
   *Do not use any media* and confirm boot order is the OS disk first.
5. First boot → console shows the web UI address. Set a **static IP: 10.x.x.x**.

### Web UI session timeout

Default 5-minute logout is annoying during setup. **User icon (top-right) →
Preferences → Session Timeout** → raise to a large value (max ≈ 2,147,482 s ≈
24 days). System-wide equivalent: System Settings → Advanced → Access → Configure.

---

## 6. Pool & datasets

### Pool: `tank`

- Layout: **single RAIDZ1 vdev, 4-wide** — the direct equivalent of the old
  RAID5 (single parity, survives one disk failure).
- Usable capacity: **~27 TiB** (3 × 10 TB worth) — maximum usable for a redundant
  layout on 4 disks.
- Encryption: **None** at pool root (best practice — avoids a single-key
  single-point-of-failure; encrypt per-dataset later if needed).

> **RAIDZ1 vs RAIDZ2:** RAIDZ1 maxes capacity but, on 10 TB drives, a second
> failure (or URE) during a multi-day resilver loses the pool. RAIDZ1 is an
> acceptable posture **because a real backup exists**; RAIDZ2 (≈18 TiB usable)
> would be the choice if relying on redundancy alone. RAIDZ expansion is
> supported, so a 5th disk can widen the vdev later.

**Create:** Storage → Create Pool → name `tank` → RAIDZ1 → 4 disks → Review →
Create Pool. (The pool name is the *one* thing that can't be renamed later —
chosen while empty to avoid regret.)

### Datasets

ZFS datasets are **not pre-sized** — they all draw from common pool free space
and grow as data is added. Quota (ceiling) and reservation (floor) are optional
and changeable anytime.

```
tank
└── storage          ← shared ONCE over SMB; child datasets appear as subfolders
    ├── movies
    ├── tv
    ├── music
    └── important     ← candidate for heavy snapshots + cloud backup
```

- All created with the **SMB preset** (case-insensitive, share-friendly ACL).
- **Only the parent `tank/storage` is shared** — never share the children
  individually (early mistake that was corrected; nested datasets surface as
  subfolders automatically).
- Optional: small **reservation** on `important` so it can't be squeezed out;
  **recordsize 1M** on `movies`/`tv` for large sequential files (leave default
  128K on `important`).

---

## 7. Shares

TrueNAS serves the same data over **both** protocols at once: SMB for Windows
desktops, NFS for the Linux VMs.

### 7.1 SMB (Windows clients)

- Authenticates by **username + password** against the dataset ACL.
- Share path: `tank/storage`. Connect from Windows to `\\10.x.x.x\storage`.

### 7.2 NFS (Linux VMs — replaces the old SSHFS workaround)

The old box used SSHFS (`files@10.x.x.x:/data`) to dodge permission/locking
grief on the old plain-NFS setup. TrueNAS does NFS properly, so SSHFS was
retired.

**Export (Shares → NFS → Add):**
- Path: `/mnt/tank/storage`
- Networks: `10.x.x.x/24`
- Advanced → **Mapall User = `aba`, Mapall Group = `aba`** (see §8)

**Client `/etc/fstab` (ellsworth, farnum):**

```
# NFS to TrueNAS
10.x.x.x:/mnt/tank/storage  /nfs/swearengen  nfs  rw,auto,nofail,noatime,_netdev,tcp  0 0
```

Dropped from the old line: `nolock` (NFSv4 locks correctly now) and the
aggressive `actimeo=1800` cache workaround. Kept `nofail` so a NAS hiccup never
blocks client boot.

**Mount & test:**

```bash
sudo umount /nfs/swearengen 2>/dev/null; sudo mount /nfs/swearengen
ls /nfs/swearengen                                   # movies tv music important
touch /nfs/swearengen/tv/.writetest && rm /nfs/swearengen/tv/.writetest
```

---

## 8. Unified identity: `aba`

**Problem:** SMB authenticates by username/password; NFS authenticates by
UID/GID. They reach the *same* dataset ACL through different doors, so ownership
must be lined up or one protocol writes while the other is denied.

**Solution — one identity for both:**

1. **Credentials → Local Users → Add** user `aba` (with a password for SMB).
   This is a TrueNAS account, separate from the `aba` logins on the VMs; its
   server-side UID does **not** need to match the clients because Mapall squashes
   all incoming NFS writes to it.
2. **`tank/storage` → Edit Permissions** → Owner `aba`, Group `aba`, **apply
   recursively** (re-owns existing files previously owned by root).
3. **NFS export → Mapall User/Group = `aba`** (clear Maproot).
4. **Remount** clients (`umount`/`mount`) so they pick up the new mapping.

Result: Windows connects as `aba` → owner → read/write; transmission writes over
NFS → squashed to `aba` → same owner. No cross-protocol ownership clashes.

> Cleanup: the old SSHFS `files` user can be deleted once no export references it
> and no other host's fstab still has a `files@10.x.x.x` SSHFS line.

---

## 9. File-management workflow (changed)

The Proxmox host can **no longer** touch this storage — the disks belong to
TrueNAS via passthrough, and ZFS pools are strictly single-host. All file work
now happens one of three ways:

1. **Over SMB from the desktop** — normal tools (7-Zip, Explorer, PowerShell);
   gated by LAN speed for big extracts.
2. **SSH into TrueNAS** (`ssh aba@10.x.x.x`) — pool lives at
   `/mnt/tank/storage/...`; `unrar`/`7z x`/`mv`/`rm` run locally at full speed.
   **Shell is for data under `/mnt` only** — manage config (datasets, shares,
   users) through the web UI, never hand-edit configs or `apt install` (appliance;
   changes don't persist).
3. **TrueNAS Apps container** (qBittorrent/SABnzbd/etc.) — keeps the whole
   fetch-extract-file loop on the NAS. Natural endgame: fold `ellsworth`'s
   transmission into an Apps container and retire the VM.

---

## 10. Portability note

A ZFS pool is self-describing — its geometry lives on the disks, identified by
GUID (not device path or controller). Moving `tank` between machines (VM ↔ bare
metal) is a supported `zpool export` → move disks → `zpool import`. On TrueNAS,
also **save a config backup** (System → General → Save Config) so users, shares,
and app settings restore alongside the pool. This is why starting in a VM is
low-risk — it doesn't lock the pool to this host.

---

## Quick reference

| Item | Value |
|------|-------|
| Proxmox host | `swearengen` (PVE 9.2.3) |
| TrueNAS VM | VMID 1000, IP **10.x.x.x** |
| Pool | `tank` — RAIDZ1, 4 × 10 TB, ~27 TiB usable |
| Share root | `tank/storage` (SMB + NFS) |
| Datasets | `movies`, `tv`, `music`, `important` |
| NFS export path | `/mnt/tank/storage`, Mapall `aba:aba`, `10.x.x.x/24` |
| Owner identity | `aba` (both protocols) |
| SATA controller | `00:17.0` `[8086:06d2]`, IOMMU group 5, vfio-pci |
| NFS clients | `ellsworth` (transmission), `farnum` |
```

