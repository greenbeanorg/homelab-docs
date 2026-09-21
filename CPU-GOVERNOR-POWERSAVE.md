# CPU Governor: Enforcing `powersave` Across Proxmox Hosts

## Table of Contents
- [Summary](#summary)
- [Background](#background)
- [Findings](#findings)
- [Fix Steps](#fix-steps)
- [Verification](#verification)
- [Quick Reference](#quick-reference)
- [Caveats & Follow-ups](#caveats--follow-ups)

---

## Summary

Audited the CPU frequency governor on all three Proxmox hosts (`swearengen`, `wu`, `garrett`) after a hot Florida summer raised the question of whether idle hosts were needlessly running at full frequency. Two of three hosts were stuck on `performance`. Set all three to `powersave` and made the setting persistent across reboots via a custom systemd unit, since Debian Trixie no longer ships a governor-management package with a usable service.

---

## Background

All three hosts use Intel CPUs on the `intel_pstate` frequency driver:

| Host | CPU | Role |
|---|---|---|
| `swearengen` | i5-10600K | Primary Proxmox host |
| `wu` | Celeron N5105 | ODROID-H3, runs OPNsense VM |
| `garrett` | i7-7700K | Newest Proxmox host, oldest hardware (16GB RAM, 1TB Intel 660p NVMe) |

The scaling governor controls whether the CPU throttles down at idle (`powersave`, `ondemand`, `schedutil`) or holds near max frequency regardless of load (`performance`). A host stuck on `performance` runs hotter and draws more power than necessary for typical homelab load.

---

## Findings

Initial check (`cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` on each host):

| Host | Governor found | Driver |
|---|---|---|
| `swearengen` | `powersave` (all cores) | `intel_pstate` |
| `wu` | `performance` (all cores) | `intel_pstate` |
| `garrett` | `performance` (all cores) | `intel_pstate` |

`swearengen` was already correct, but on inspection nothing was actually enforcing it — no `cpupower`/`cpufrequtils` package installed, no systemd unit, no GRUB cstate override in `/proc/cmdline`. It was simply the kernel/driver's current-boot default, not a persisted setting. `wu` and `garrett` had no such luck and came up in `performance`.

Attempted the traditional fix (`cpufrequtils`) first — not available: Debian Trixie dropped the `cpufrequtils` package. `linux-cpupower` (the `cpupower` tool) is its replacement, but the Trixie package doesn't ship a working `cpupower.service` unit, so enabling that service failed on both hosts.

---

## Fix Steps

1. **Immediate governor change** (all cores, per host):
   ```bash
   for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo powersave > $c; done
   ```

2. **Persist across reboots** — since neither `cpufrequtils` nor `cpupower.service` panned out, created a custom oneshot systemd unit on each host:
   ```bash
   cat > /etc/systemd/system/cpu-powersave.service << "EOF"
   [Unit]
   Description=Set CPU governor to powersave
   After=multi-user.target

   [Service]
   Type=oneshot
   ExecStart=/bin/sh -c "for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo powersave > $c; done"

   [Install]
   WantedBy=multi-user.target
   EOF
   ```

3. **Enable it:**
   ```bash
   systemctl daemon-reload
   systemctl enable --now cpu-powersave.service
   ```

Applied to `wu`, `garrett`, and `swearengen` (all three, for consistency — `swearengen` had the right governor but no enforcement mechanism).

---

## Verification

Governor, post-fix, all three hosts:
```bash
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```
All cores on all three hosts report `powersave`.

Unit status:
```bash
systemctl status cpu-powersave.service --no-pager
```
Expect `active (exited)` with a clean exit code.

**Still pending:** an actual reboot test on each host to confirm the unit fires correctly at boot (ordering after `intel_pstate` loads) rather than just working when run manually via `systemctl restart`. Plan is to reboot `garrett` first (lowest-impact host), then `wu` at a window that won't disrupt the OPNsense VM it hosts, then `swearengen` last given it carries the most VMs.

---

## Quick Reference

| Check | Command |
|---|---|
| Current governor | `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` |
| Frequency driver | `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver` |
| Force powersave now | `for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo powersave > $c; done` |
| Unit status | `systemctl status cpu-powersave.service --no-pager` |
| GRUB cstate overrides | `cat /proc/cmdline` |

---

## Caveats & Follow-ups

- The systemd unit has **not yet been validated across an actual reboot** on any host — only via `enable --now`, which runs it immediately but doesn't prove correct boot-time ordering.
- Root cause of why `wu` and `garrett` came up in `performance` in the first place (vs. `swearengen`'s `powersave` default) is unconfirmed — possibly an installer default, BIOS/EFI power profile hint, or an unrecorded prior tuning attempt.
- `cpupower` (the CLI tool) is still installed on `wu` and `garrett` and works fine for ad-hoc inspection (`cpupower frequency-info`) even though its bundled service isn't usable — the enforcement is entirely via the custom unit, not the package's own tooling.
- Deeper idle power saving (C-states) was not audited in this pass — governor only. `turbostat` or a C-state check would be the natural next step if further heat/power reduction is wanted, since `powersave` alone doesn't guarantee deep sleep state usage.
