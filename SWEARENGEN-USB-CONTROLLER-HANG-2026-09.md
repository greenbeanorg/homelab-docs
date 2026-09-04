# swearengen Hard Freeze: Shared xHCI Controller Wedge

Root-cause runbook for a full hypervisor hang on `swearengen` (i5-10600K/48GB, primary Proxmox host) traced to a wedged USB controller shared between the UPS and other USB peripherals.

---

## 1. Symptom

- `swearengen` completely unresponsive: text `login:` prompt frozen, cursor not blinking, no response to any keypress, VT switching (Alt-F2/F3/F4) dead.
- Physical HDDs still spinning — ruled out full power loss.
- No remote access (SSH, Proxmox web UI) — full hang, not just a display issue.
- First external signal: Healthchecks.io dead-man's-switch (`greenbeanorg-kuma-reachable`) fired DOWN at `2026-09-04 03:05:02 -0400`.
- Recovery required a hard power cycle; no graceful shutdown was possible.

---

## 2. Root Cause

**A wedged USB controller, not thermal, memory, CPU, or ZFS.**

`swearengen`'s motherboard exposes only **one physical xHCI controller** (`0000:00:14.0`). Both USB buses shown in `lsusb -t` (bus 001 / 480M and bus 002 / 10000M) are the USB2 and USB3 halves of that same single controller — there is no hardware redundancy. This controller serves:
- The CyberPower UPS (`CP1500PFCLCDa`, USB HID)
- An ASUS AURA LED controller with a known malformed USB descriptor
- The motherboard's rear USB header (used for emergency keyboard access)

### Evidence chain

| Time (Sep 4) | Event |
|---|---|
| 01:23:22 | `usbhid-ups[1196]: nut_libusb_get_string/get_report: Input/Output Error` begins |
| 01:23–02:53 | Continuous I/O errors and pipe errors from `usbhid-ups` against the UPS HID interface (dozens of occurrences over 90 min) |
| 02:53:06 | **Last line in the entire boot's journal** — another `nut_libusb_get_string: Pipe error` |
| ~03:05 | System fully unresponsive; Healthchecks.io detects missed check-in |
| — | No panic, OOM, MCE/EDAC event, soft-lockup, hung-task, or NMI-watchdog message anywhere in the boot |

⚠️ **The absence of a kernel panic trace is itself the diagnostic signal.** A kernel that detects its own failure (OOM, panic, watchdog trip) almost always logs something before going dark. A total silence *after* a USB driver error storm, with SATA-attached drives unaffected, points at the USB host controller hanging — not the kernel/CPU/memory subsystem. This also explains why a USB keyboard plugged into the motherboard got zero response: it shared the same wedged controller as the UPS.

### Ruled out
- **Thermal**: CPU idling 33–35°C at time of check, nowhere near throttle (`high=80°C`). No `thermal`/`throttl` kernel log matches in the crashed boot.
- **Memory/CPU (MCE/EDAC)**: Only line matching was EDAC module init at boot; no actual error events.
- **ZFS**: TrueNAS pools (`boot-pool`, `tank`) both `ONLINE`, no errors, last scrubs clean. (Note: TrueNAS is a separate host from swearengen — checked as due diligence given prior restic/ZFS issues, not implicated here.)
- **NCT6798 voltage `ALARM` flags**: False positives — known quirk where this Super I/O chip's `min`/`max` thresholds default to 0.00V, tripping alarms on any nonzero reading. Not a real power issue.

---

## 3. Fixes Applied

### 3.1 Disabled the AURA LED controller
Confirmed via `dmesg` the ASUS AURA device (`idVendor=0b05, idProduct=1939`) throws malformed descriptor errors on every enumeration:
```
usb 1-13: config 1 has an invalid interface number: 2 but max is 1
usb 1-13: config 1 has no interface number 1
```
It was being claimed by generic `usbhid`, not `hid_asus` (confirmed via `lsmod | grep -i hid` before the fix) — a module blacklist alone would not have stopped enumeration. Used a udev authorization rule instead:
```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0b05", ATTR{idProduct}=="1939", ATTR{authorized}="0"' \
  | sudo tee /etc/udev/rules.d/99-block-aura.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
```
Verified with `lsusb -t` — port 013 now shows no driver bound at all.

⚠️ A `blacklist hid_asus` entry in `/etc/modprobe.d/` was also added defensively but has **no effect on its own** since that driver was never loaded in the first place — the udev `authorized=0` rule is what actually does the work.

### 3.2 Hardware watchdog via systemd
No standalone `watchdog` package — installing it wants to remove `proxmox-ve`, `pve-manager`, `qemu-server`, `pve-container`, and `pve-ha-manager` to resolve a `/dev/watchdog` ownership conflict with `pve-ha-manager`'s `watchdog-mux`. **Do not force this** (`touch /please-remove-proxmox-ve`) — it strips the Proxmox management stack.

Used systemd's built-in watchdog support instead, since HA is not in use on this host:
```bash
sudo tee -a /etc/systemd/system.conf << 'EOF'
RuntimeWatchdogSec=60
RebootWatchdogSec=10min
EOF
sudo systemctl daemon-reexec
```
Effect: systemd pets `/dev/watchdog` roughly every 20s; if it stops for 60s (full kernel/system hang), the hardware watchdog forces a reboot with no manual intervention required.

⚠️ `RuntimeWatchdogUSec` reflects the new value immediately after `daemon-reexec` (confirmed `1min`), but the **hardware chip's own timeout** (`/sys/class/watchdog/watchdog0/timeout`) does not update until an actual reboot — `daemon-reexec` is not sufficient. Verify both agree after the next real reboot.

---

## 4. Outstanding / Follow-up

- [ ] Reboot `swearengen` (next natural opportunity) and confirm `systemctl show -p RuntimeWatchdogUSec` and `cat /sys/class/watchdog/watchdog0/timeout` agree (~60s / 1min)
- [ ] Swap or reseat the CyberPower UPS USB cable — cheap first step, addresses the likely proximate trigger (flaky captive cable) independent of the controller-sharing root cause
- [ ] Watch for recurrence: `journalctl -f | grep -i usbhid-ups`
- [ ] **Migrate UPS off local USB entirely** — move to a Pi 2B (or similar low-power host) running as NUT server; `swearengen` becomes a NUT netclient (`MODE=netclient` in `/etc/nut/nut.conf`). This is the actual fix for controller-sharing risk — cable swap and watchdog are mitigations, not a cure. Blocked on hardware availability.
- [ ] If a PCIe USB expansion card is ever added, put the UPS on that instead for genuine hardware isolation from onboard USB.

---

## 5. Known Limitations

- This board has exactly one physical USB controller — any USB device attached to it (UPS, RGB controllers, keyboards, etc.) shares fate with all others until the Pi migration is done.
- The systemd watchdog masks a repeat of this exact failure (auto-reboot instead of a dead box) but does not prevent the underlying wedge — VMs will still go down uncleanly if it fires.
- Root cause is diagnosed by elimination and log-silence correlation, not by a definitive stack trace — netconsole to a second host is not yet in place, so a genuinely inexplicable future hang would still leave a blind spot. Worth adding if this recurs.

---

## 6. Quick Reference

| Check | Command |
|---|---|
| Boot list / find previous crashed boot | `journalctl --list-boots` |
| Previous boot kernel log tail | `journalctl -b -1 -k --no-pager \| tail -200` |
| MCE/EDAC check | `journalctl -b -1 -k \| grep -iE 'mce\|edac\|machine check'` |
| ZFS pool health | `zpool status -v` / `zpool events -v` |
| Thermals | `sensors` |
| USB topology | `lsusb -t` |
| USB kernel events | `dmesg \| grep -iE 'usb\|xhci'` |
| UPS status | `upsc <ups_name>@localhost` |
| Watchdog config (systemd side) | `systemctl show -p RuntimeWatchdogUSec` |
| Watchdog config (hardware side) | `cat /sys/class/watchdog/watchdog0/timeout` |
| Force udev re-eval without reboot | `udevadm trigger --subsystem-match=usb` |

---

*Publish checklist: verify all internal anchor links resolve; confirm no real credentials/MACs present (IPs in this doc are hostnames only, no RFC1918 addresses to mask); run dead-link curl loop against README index after commit.*
