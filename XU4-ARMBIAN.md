# ODROID-XU4: Ubuntu 24.04 → Armbian 26.04 Migration

**Status:** Complete
**Host:** ODROID-XU4 (network-wide Pi-hole DNS)

## Purpose

Migration of an ODROID-XU4 from Ubuntu 24.04 LTS running a Hardkernel-specific kernel
and U-Boot configuration to a current Armbian installation based on Ubuntu 26.04 LTS.

Documents both **why a normal `do-release-upgrade` was not chosen** and how the board
was migrated to a currently maintained OS.

## Hardware

| | |
|---|---|
| Board | Hardkernel ODROID-XU4 |
| SoC | Samsung Exynos 5422 |
| CPU architecture | ARMv7 / AArch32 (Debian arch: `armhf`) |
| Boot media | eMMC |
| Boot partition | FAT16 |
| Root filesystem | ext4 |

Ubuntu officially supports `armhf`, so the XU4's architecture was never itself the blocker.

## 1. Original system

```
Description: Ubuntu 24.04.4 LTS
Release:     24.04
Codename:    noble
Kernel:      6.1.90-21 (armv7l)
Arch:        armhf
```

Identified with `lsb_release -a`, `uname -a`, `dpkg --print-architecture`.

## 2. Why `do-release-upgrade -d` was not used

```
$ do-release-upgrade -c
There is no development version of an LTS available.

$ do-release-upgrade -d -c
New release '26.04 LTS' available.
```

⚠️ The `-d` flag was **not** evidence that 26.04 was beta — 26.04 LTS is a released LTS
(April 2026, security maintenance through May 2031). The real blocker was the
board-specific kernel and boot environment.

## 3. The kernel situation

The XU4 was not running Ubuntu's generic kernel:

```
linux-odroid-5422-next   6.1.90-21
odroid-platform-5422     20240910-5
bootini                  20240507-2
mali-fbdev               3:2024031412

linux-image-generic:  Installed: (none)
```

And the ODROID kernel had no current APT candidate:

```
linux-odroid-5422-next:
  Installed: 6.1.90-21
  Candidate: 6.1.90-21
Version table:
 *** 6.1.90-21 100
     100 /var/lib/dpkg/status
```

**The kernel existed only as a locally installed package** — no repository could supply a
successor. No package holds were in place (`apt-mark showhold` returned nothing).

APT was configured for `noble` against `ports.ubuntu.com`; the old ODROID repo survived
only as an inactive `/etc/apt/sources.list.d/odroid.list.save` pointing at
`deb http://deb.odroid.in/5422-s/ focal main`.

So the installed system was: **Ubuntu 24.04 userspace + Hardkernel 5422 kernel + ODROID
boot configuration** — not an Ubuntu-native kernel/boot stack.

## 4. ⚠️ `/boot` is not the boot partition

The most important discovery, and the one that misleads most XU4 troubleshooting:

```
mmcblk1
├── mmcblk1p1  vfat FAT16 LABEL=boot   → mounted at /media/boot   ← actual boot partition
└── mmcblk1p2  ext4       LABEL=rootfs → mounted at /
```

The `/boot` directory on the root filesystem is **not** what U-Boot reads. The real boot
files live on the FAT partition at `/media/boot`.

## 5. U-Boot boot flow

From `boot.ini`:

```
fatload mmc 0:1 ${k_addr} zImage
fatload mmc 0:1 ${initrd_loadaddr} uInitrd
bootz ${k_addr} ${initrd_loadaddr} ${dtb_loadaddr}
```

```
XU4 ROM → U-Boot → MMC 0:1 ─┬─ zImage
                            ├─ uInitrd
                            └─ exynos5422-odroidxu4.dtb
                                  ↓
                            Linux 6.1.90-21 → Ubuntu 24.04 armhf
```

Kernel command line pinned the root by UUID (`root=UUID=e139ce78-…`), matching
`/dev/mmcblk1p2` — confirming the boot config was deliberately tied to that rootfs.

Boot partition contents:

```
/media/boot/
├── boot.ini
├── config.ini
├── exynos5422-odroidxu4.dtb   (Jul 26 2024)
├── overlays/
├── uInitrd                    (Sep 12 2024)
└── zImage                     (Jul 26 2024)
```

**U-Boot loads fixed filenames.** Changing kernels is therefore not just a matter of
installing an Ubuntu kernel package.

## 6. Decision

The question was never *"can an XU4 run Ubuntu 26.04?"* — it can. The question was
*"what board-specific kernel and boot configuration would the XU4 use after a userspace
upgrade?"*

With the ODROID kernel orphaned (no active source for a successor), a release upgrade
risked leaving **Ubuntu 26.04 userspace + an unmaintained 2024 kernel**.

**Chosen strategy: flash a current Armbian XU4 image directly to the eMMC.**

Rationale:
- Not a production-critical host; nothing needed preserving in place
- Existing kernel dated 2024, package orphaned
- Armbian provides a current, maintained XU4-specific boot/kernel stack
- Armbian offers a Ubuntu 26.04 (Resolute) userspace option
- eMMC can be written externally via USB adapter — clean rollback path

Image at time of migration: **Armbian 26.5.1 Minimal/IOT, kernel 6.6.141**
(<https://www.armbian.com/odroid-xu4/>)

## 7. Pre-migration backup

```bash
uname -a; lsb_release -a; dpkg --print-architecture
lsblk -f; ip addr; ip route

dpkg --get-selections > package-list.txt
tar czf apt-config.tar.gz /etc/apt
tar czf etc-config.tar.gz /etc
```

For full rollback capability, take a block-level image of the eMMC before overwriting.

## 8. Flashing

1. Remove the eMMC module from the XU4, attach via USB eMMC adapter
2. ⚠️ **Verify device identity — do not assume `/dev/sdX`:**
   `lsblk -o NAME,SIZE,MODEL,SERIAL,TRAN`
3. Write the image to the **whole device**, not a partition (Etcher or Armbian's
   recommended tool)
4. Eject, reinstall in the XU4, connect Ethernet, power on

## 9. Verification

```bash
uname -a                    # kernel is no longer 6.1.90-21
uname -r
dpkg --print-architecture   # armhf
cat /etc/os-release         # VERSION_ID="26.04"
lsblk -f
findmnt /boot
findmnt /media/boot

apt update && apt full-upgrade
ip addr; ip route; ping -c 3 1.1.1.1
getent hosts ubuntu.com
lsmod
systemctl --failed          # expect: 0 loaded units listed
df -h
```

## 10. Rollback

Power off → remove Armbian eMMC → restore original eMMC (or original disk image) →
reinstall → verify boot. Straightforward precisely because the original install was
never modified in place.

## Key lessons

- **Architecture support was not the problem.** XU4 is `armhf`; Ubuntu supports `armhf`
  including 26.04. The problem was board-specific boot/kernel integration.
- **`/boot` was misleading** — the real boot partition was `/media/boot` (FAT16). Critical
  when troubleshooting any ODROID boot issue.
- **U-Boot loads fixed filenames** (`zImage`, `uInitrd`) plus a board DTB, so kernel
  changes are a boot-partition operation, not just a package operation.
- **An orphaned vendor kernel is a migration blocker.** When the installed kernel package
  has no repository candidate, a userspace-only upgrade strands the system.

## Final state

```
OLD                                NEW
U-Boot                             U-Boot / Armbian boot config
  ↓                                  ↓
Hardkernel zImage / uInitrd        Current XU4-compatible kernel (6.6.x)
  ↓                                  ↓
Linux 6.1.90-21                    Ubuntu 26.04 LTS armhf userspace
  ↓
Ubuntu 24.04 armhf
```

## References

- [Ubuntu supported architectures](https://ubuntu.com/project/docs/how-ubuntu-is-made/concepts/supported-architectures/)
- [Ubuntu 26.04 lifecycle](https://ubuntu.com/about/release-cycle?product=ubuntu&release=ubuntu&version=26.04+LTS)
- [Armbian ODROID-XU4](https://www.armbian.com/odroid-xu4/)
