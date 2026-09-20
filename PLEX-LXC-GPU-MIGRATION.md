# Migrating Plex from a VM to an LXC with Intel QuickSync Passthrough

**Host:** swearengen (i5-10600K, UHD 630 iGPU + AMD Radeon R9 290 discrete GPU)
**Old guest:** farnum (VM, VMID 100) — software transcoding only
**New guest:** LXC, VMID 301 (300s block reserved for LXC containers) — hostname/IP reused as `farnum` / 10.79.20.110 after cutover

Plex-on-a-homelab is the ultimate cliché, but this migration turned up enough genuinely sharp edges that it's worth documenting rather than re-discovering next time.

## Why LXC instead of VM passthrough

LXC shares the host kernel, so GPU passthrough is a device bind-mount rather than full PCI passthrough into an isolated VM kernel. Much simpler for a single iGPU you still want the host to be able to use for other things.

## 1. BIOS: re-enable the iGPU

If the host also has a discrete GPU installed, many boards **disable the iGPU entirely** the moment they detect a PCIe card — it won't even show up in `lspci` on the host, let alone be passable into a container.

Check first:
```bash
lspci -nn | grep -i intel | grep -iE "vga|display|3d|0300|0380"
```
If nothing shows up, go into BIOS/UEFI and look for a setting like **"Primary Display" / "iGPU Multi-Monitor" / "Internal Graphics"** and set it to allow both the iGPU and the discrete card (not just the discrete one). Requires a reboot of the host — plan a maintenance window if it's your primary hypervisor.

After rebooting, confirm:
```bash
lspci -nn | grep -i intel
```
Look for a `Display controller [0380]` entry (Intel iGPUs often show as `[0380]`, not `[0300]`, when a discrete GPU is the primary display device — that's normal, not a sign it's non-functional).

## 2. Identify the correct `/dev/dri` device — don't guess

If there's more than one GPU in the box, `/dev/dri/card0`, `card1`, etc. are **not** guaranteed to map predictably to a specific physical GPU, and the numbering can shift after a BIOS change re-enables a device. Confirm by PCI address, every time:

```bash
lspci -nn | grep -iE "vga|display|3d"    # get each GPU's PCI address
ls -la /dev/dri/by-path/                  # match address to card/render node
```

Example output:
```
pci-0000:00:02.0-card -> ../card1        # Intel iGPU
pci-0000:00:02.0-render -> ../renderD128
pci-0000:01:00.0-card -> ../card2        # AMD discrete
pci-0000:01:00.0-render -> ../renderD129
```

Using the wrong node here won't produce an obvious error — the container will pass through fine but VA-API will fail with something like:
```
DRM_IOCTL_VERSION, unsupported drm device by media driver: amdg
libva error: /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so init failed
```
That specific error means you passed through an AMD device to the Intel (`iHD`) driver — go back and re-check `by-path`.

## 3. Create the LXC and pass the GPU through

```bash
pct create 301 local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst \
  --hostname plex --cores 4 --memory 4096 \
  --rootfs local-lvm:32 --net0 name=eth0,bridge=vmbr0,tag=20,ip=dhcp \
  --unprivileged 0 --features nesting=1
```

Edit `/etc/pve/lxc/301.conf` and add (numbers from step 2 — major/minor from `ls -la /dev/dri/`):
```
lxc.cgroup2.devices.allow: c 226:1 rwm
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri/card1 dev/dri/card1 none bind,optional,create=file
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file
```

```bash
pct start 301
pct enter 301
```

## 4. Fix GID mismatch inside the container

The device nodes on the host are owned by specific GIDs (e.g., `video` for `card1`, `render` for `renderD128`), but **inside the container those same numeric GIDs may belong to a totally different, unrelated group** (in this case, `postfix` happened to own GID 128 in the fresh Debian 13 template). Group *names* don't matter for device permission checks — only the numeric GID does.

Check what actually owns each GID inside the container:
```bash
ls -la /dev/dri/     # note the group names shown
getent group render  # confirm what GID that name maps to in THIS container
```
If they don't line up, add the `plex` user to whatever group currently holds the matching GID, however oddly named:
```bash
usermod -aG video,render,postfix plex   # adjust to match your actual output
```

## 5. Install VA-API driver — enable non-free repo first

Debian trixie's default sources don't include `non-free`, and `intel-media-va-driver-non-free` (the correct driver for real QuickSync encode support — the plain `i965-va-driver` is legacy and incomplete for newer hardware) lives there.

```bash
sed -i 's/Components: contrib main/Components: contrib main non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources
apt update
apt install -y intel-media-va-driver-non-free vainfo
```

## 6. Verify VA-API — force the driver and device explicitly

`vainfo` alone often autodetects the wrong backend (tried `radeonsi` on a box with both an Intel and AMD GPU present, even when told to use the Intel render node):
```bash
LIBVA_DRIVER_NAME=iHD vainfo --display drm --device /dev/dri/renderD128
```
A working result lists real profiles: `VAProfileH264High`, `VAProfileHEVCMain10`, etc., both `VAEntrypointVLD` (decode) and `VAEntrypointEncSlice` (encode).

Make it persistent so Plex itself (not just your shell) uses the right driver:
```bash
echo 'LIBVA_DRIVER_NAME=iHD' >> /etc/environment
pct reboot 301
```

## 7. Install Plex — skip the apt repo, use the .deb directly

As of ~Feb 2026, Debian trixie's sequoia-based signature checker rejects Plex's apt repo key because its self-signature uses SHA1, which trixie's policy no longer trusts:
```
Signing key ... is not bound ... SHA1 is not considered secure since 2026-02-01
```
Simplest fix: skip the repo, download and install the `.deb` directly (Plex self-updates from within the app afterward anyway):
```bash
wget <direct .deb link from plex.tv/media-server-downloads>
dpkg -i plexmediaserver_*.deb
apt-get install -f
```

## 8. Media library mount — LXC can't mount NFS directly

Unprivileged/privileged LXC containers generally **cannot mount NFS shares from inside the container** — the kernel namespace lacks the privileges the NFS client needs:
```
mount: fsconfig() failed: NFS: mount program didn't pass remote address.
```
Fix: mount the NFS share on the **Proxmox host**, then bind-mount it into the container via its config — don't put an NFS client inside the container at all.

```bash
# on the host:
mkdir -p /mnt/nfs-media
mount -t nfs <nfs-server>:/path/to/media /mnt/nfs-media
# make persistent in the HOST's /etc/fstab

# then bind it into the container:
pct set 301 -mp0 /mnt/nfs-media,mp=/nfs/swearengen
pct reboot 301
```
Remove any NFS line from the container's own `/etc/fstab` — it will never work there.

## 9. Migrate data — stop Plex *completely* before rsync

```bash
# on the OLD server, before copying anything:
systemctl stop plexmediaserver
systemctl status plexmediaserver   # confirm actually stopped, not just stopping
rsync -avP "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/" \
  root@<new-ct-ip>:"/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/"
```
**If Plex is still running during the rsync**, its SQLite database can get copied mid-write and end up truncated/corrupted. Symptom on first start of the new instance:
```
libc++abi: terminating with uncaught exception of type soci::soci_error:
sqlite3_statement_backend::prepare: database disk image is malformed
```
Recovery: Plex keeps daily database backups in the same directory —
```bash
ls -la ".../Plug-in Support/Databases/"
# look for com.plexapp.plugins.library.db-YYYY-MM-DD (dated backups, ~30MB+)
# compare size against the live .db file — a live file only tens of KB in size
# next to ~30MB dated backups is the smoking gun for a truncated database
```
Restore the most recent good one:
```bash
systemctl stop plexmediaserver
mv com.plexapp.plugins.library.db com.plexapp.plugins.library.db.broken
cp com.plexapp.plugins.library.db-<latest-date> com.plexapp.plugins.library.db
rm -f com.plexapp.plugins.library.db-shm com.plexapp.plugins.library.db-wal
chown plex:plex com.plexapp.plugins.library.db
systemctl start plexmediaserver
```
On the new server, after rsync completes: `chown -R plex:plex` on the whole Application Support directory before starting Plex (rsync as root leaves root ownership).

## 10. Verify hardware transcoding is actually engaging

Checkbox being on in Plex's settings doesn't confirm it's working — verify directly:
```bash
# on the HOST:
apt install -y intel-gpu-tools
intel_gpu_top
```
Force a transcode in Plex (play something and manually select a lower quality than the source in the player's quality menu), and watch for non-zero activity on the **Video** / **VideoEnhance** engine rows while it plays. Zero activity during a forced transcode means Plex silently fell back to software (libx264) despite the setting being checked.

## 11. Cut over hostname/IP, then decommission the old VM

Get the old VM's IP first (if not statically documented anywhere):
```bash
qm guest cmd <old-vmid> network-get-interfaces   # requires qemu-guest-agent running
```
Then, once the new LXC is fully verified:
```bash
qm stop <old-vmid>
qm destroy <old-vmid>

pct set <new-ctid> --hostname <old-hostname>
pct set <new-ctid> -net0 name=eth0,bridge=vmbr0,tag=<vlan>,ip=<old-ip>/24,gw=<gateway>
pct reboot <new-ctid>
```
Existing DNS records, Uptime Kuma monitors, and anything else pointed at the old hostname/IP keep working without changes.

## Summary of gotchas hit (for quick scanning)

| Symptom | Cause | Fix |
|---|---|---|
| iGPU missing from `lspci` entirely | BIOS disables iGPU when a discrete GPU is installed | Enable "iGPU Multi-Monitor" / equivalent in BIOS |
| VA-API error mentioning `amdgpu`/`radeonsi` | Wrong `/dev/dri` node passed through (multi-GPU box) | Match PCI address via `/dev/dri/by-path/`, not just card number |
| Plex install shows "Video Group: postfix" | Container GID namespace doesn't match host GID names | Add `plex` user to whatever group holds the matching numeric GID |
| `intel-media-va-driver-non-free` has no install candidate | Debian trixie's default sources exclude `non-free` | Add `non-free non-free-firmware` to `Components:` in sources file |
| Plex apt repo `InRelease` fails signature check (SHA1) | Debian trixie's sequoia policy rejects Plex's SHA1-signed key | Install the `.deb` directly instead of using the apt repo |
| `mount: fsconfig() failed: NFS: mount program didn't pass remote address` | LXC can't mount NFS from inside the container | Mount NFS on the host, bind-mount into the container instead |
| Plex crashes on start: `database disk image is malformed` | Source Plex instance wasn't fully stopped before rsync | Restore from Plex's automatic dated DB backup |
