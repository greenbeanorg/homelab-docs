# UPS — CyberPower Monitoring via NUT

UPS monitoring for the lab, running **NUT** in a netserver/netclient topology
across three hosts, each with its own shutdown trigger point.

A dedicated Raspberry Pi 2B owns the USB connection and serves UPS state over
the network. `swearengen` and the TrueNAS SCALE VM both consume it as clients,
with deliberately different trigger thresholds so storage always comes down
before the hypervisor.

- **NUT primary:** `pi2b` — Raspberry Pi 2 Model B v1.1, USB to the UPS — **10.x.x.101**
- **NUT secondary:** `swearengen` — Proxmox VE 9.2 host — **10.x.x.5**
- **NUT secondary:** TrueNAS SCALE VM — **10.x.x.20**
- **UPS:** CyberPower CP1500PFCLCDa — 1500 VA / 1000 W
- **Driver:** `usbhid-ups`
- **Rollout:** complete across all three hosts, reboot-tested
- **Date:** September 2026 (migrated from `swearengen`-as-primary; see [§1.1](#11-why-this-moved))

| Question | Document |
| --- | --- |
| **NUT topology, shutdown coordination, host config** | **this document** |
| Why the TrueNAS **Reporting → UPS** page needs its own config | [TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md) |
| The incident that motivated moving the UPS off `swearengen` | [SWEARENGEN-USB-CONTROLLER-HANG-2026-09.md](SWEARENGEN-USB-CONTROLLER-HANG-2026-09.md) |

---

## 1. Design

[#1-design](#1-design)

```text
CyberPower UPS (USB)
        |
        v
pi2b - Raspberry Pi 2B v1.1        10.x.x.101
   +-- usbhid-ups  (driver)
   +-- upsd        (serves state on :3493)
   +-- upsmon      (primary, local monitor)
   |
   +---- network ----> swearengen (Proxmox host)   10.x.x.5
   |                     upsmon (secondary) - triggers LATE
   |                     (low battery / FSD, near end of runtime)
   |
   +---- network ----> TrueNAS SCALE VM             10.x.x.20
                         upsmon (secondary) - triggers EARLY
                         (ONBATT + 90s grace timer)
```

### 1.1 Why this moved

[#11-why-this-moved](#11-why-this-moved)

The UPS's USB connection previously lived on `swearengen` itself. That board
has exactly **one physical xHCI controller**, shared between the UPS, an
onboard RGB LED controller with a known-buggy descriptor, and the motherboard's
own USB header. A wedge on that controller took the entire hypervisor down —
full root-cause writeup in
[SWEARENGEN-USB-CONTROLLER-HANG-2026-09.md](SWEARENGEN-USB-CONTROLLER-HANG-2026-09.md).

Moving the UPS's only physical dependency onto a separate, single-purpose host
removes that failure mode entirely: a USB wedge on `pi2b` can no longer take
`swearengen` down with it, and vice versa.

### 1.2 Shutdown ordering

**The device that owns USB is not the device that should shut down last.**
`pi2b` holds the hardware connection, but has no VMs, no pool, and nothing
that benefits from staying up longest — so it stays a passive relay, and
shutdown authority (`SHUTDOWNCMD`, the ability to kill outlet power) is
reserved to it but not delegated further down the chain by default.

| Stage | Host | Trigger | Why |
| --- | --- | --- | --- |
| 1st | TrueNAS VM | `ONBATT` + 90s grace timer | Exports the ZFS pool cleanly, well before anyone else reacts |
| 2nd | Other guests | Proxmox shutdown sequence | Ordinary guest shutdown, cascaded from the host |
| 3rd (last) | `swearengen` | low battery / stale-runtime, `DEADTIME 120` | Host shuts down only after storage is already quiesced |

TrueNAS's trigger is deliberately **earlier and independent** of `swearengen`'s
— it does not wait for the host to decide anything. This avoids racing a
shared shutdown-timeout budget: Proxmox's per-VM cascade timing is not
something you want your storage layer's clean-export window to depend on. See
[§6](#6-truenas-as-a-netclient) for the actual timer.

---

## 2. Install and confirm detection — on `pi2b`

[#2-install-and-confirm-detection](#2-install-and-confirm-detection)

```bash
sudo apt update
sudo apt install nut nut-server nut-client -y
```

Confirm the UPS is present on USB:

```bash
lsusb
```

```text
0764:0601 Cyber Power System, Inc.
```

---

## 3. Driver

[#3-driver](#3-driver)

`/etc/nut/ups.conf`:

```ini
[cyberpower]
    driver = usbhid-ups
    port = auto
    desc = "CyberPower CP1500PFCLCDa"
```

### Warning: USB permissions - the step that actually blocks first boot

The `nut` user must own the device node or `usbhid-ups` fails outright with
`insufficient permissions on everything`, and `upsc` reports the generic
`Driver not connected` — which looks like a driver problem, not a permissions
one.

The stock udev rules (`/lib/udev/rules.d/62-nut-usbups.rules`) already ship
CyberPower's vendor/product ID (`0764:0601`) correctly. The failure mode
encountered here wasn't a missing rule — it was the rule not having been
**applied** to a device that was already connected before the rule file was
in place:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
ls -l /dev/bus/usb/001/00N        # expect: root nut, not root root
```

If group ownership is still `root` after that, confirm the `nut` group
actually exists (`getent group nut`) before assuming the rule itself is bad.

Test the driver directly before going further:

```bash
sudo systemctl enable --now nut-driver@cyberpower
upsc cyberpower@localhost
```

Expect battery charge, runtime, load, input voltage, and `ups.status: OL`.

---

## 4. Server

[#4-server](#4-server)

`/etc/nut/nut.conf`:

```ini
MODE=netserver
```

`/etc/nut/upsd.conf`:

```ini
LISTEN 0.0.0.0 3493
```

`/etc/nut/upsd.users` — one entry per client host, not a shared credential.
Kept separable so any one host's access can be revoked without touching the
others:

```ini
[monmaster]
    password = ${UPS_MONMASTER_PASSWORD}
    upsmon master

[truenas]
    password = ${UPS_TRUENAS_PASSWORD}
    upsmon slave
```

The `upsmon master`/`slave` keyword here only grants the *permission* to
request that role — it doesn't force it. The role actually taken is set by
each host's own local `MONITOR` line in its `upsmon.conf` ([§5](#5-monitor)).
Only `pi2b`'s own local monitor (watching `cyberpower@localhost`) should
actually run as primary; every remote client — `swearengen` included — runs
`slave`/secondary.

---

## 5. Monitor

[#5-monitor](#5-monitor) — configuration on `pi2b` itself

`/etc/nut/upsmon.conf`:

```ini
MONITOR cyberpower@localhost 1 monmaster ${UPS_MONMASTER_PASSWORD} master
```

```bash
sudo systemctl enable --now nut-server nut-monitor
```

### On `swearengen` (secondary)

```bash
sudo systemctl stop nut-driver@cyberpower nut-server nut-monitor
sudo systemctl disable nut-driver@cyberpower nut-server nut-monitor
sudo apt install nut-client -y
```

`/etc/nut/nut.conf`:

```ini
MODE=netclient
```

`/etc/nut/upsmon.conf`:

```ini
MONITOR cyberpower@10.x.x.101 1 monmaster ${UPS_MONMASTER_PASSWORD} slave
```

### Warning: DEADTIME needs raising once the UPS is off local USB

The default (`15`) assumes near-instant local driver recovery. Over a network
hop, a routine reboot of the primary host easily exceeds that, and a
stale-data declaration under `MINSUPPLIES 1` risks triggering `SHUTDOWNCMD`
unnecessarily.

```ini
DEADTIME 120
```

Must remain a multiple of `POLLFREQ` (default `5`).

Also worth enabling, otherwise a communication loss with the primary is
silent:

```ini
NOTIFYFLAG ONLINE   SYSLOG
NOTIFYFLAG ONBATT   SYSLOG+WALL
NOTIFYFLAG LOWBATT  SYSLOG+WALL
NOTIFYFLAG COMMBAD  SYSLOG+WALL
NOTIFYFLAG COMMOK   SYSLOG
NOTIFYFLAG NOCOMM   SYSLOG+WALL
NOTIFYFLAG FSD      SYSLOG+WALL
```

```bash
sudo systemctl enable --now nut-monitor
```

---

## 6. TrueNAS as a netclient

[#6-truenas-as-a-netclient](#6-truenas-as-a-netclient)

**System Settings → Services → UPS**, configured through the UI — TrueNAS
middleware regenerates NUT config on its own schedule, so hand-editing
`/etc/nut/` inside the VM gets silently reverted.

| Field | Value |
| --- | --- |
| UPS Mode | Slave |
| Remote Host | `10.x.x.101` |
| Remote Port | `3493` |
| Identifier | `cyberpower` |
| Monitor User | `truenas` |
| Monitor Password | as set in `upsd.users` on `pi2b` |
| Shutdown Mode | UPS goes on battery |
| Shutdown Timer | `90` |

### Warning: leave "Power Off UPS" unchecked

That option tells NUT to cut outlet power to the whole UPS once TrueNAS's own
shutdown completes — including `pi2b` and anything else plugged in. Kill-power
authority stays reserved to the primary, not delegated to a secondary client.

The UI writes this into `/etc/nut/upsmon.conf` and `/etc/nut/upssched.conf` as
a real timer/cancel pair, not just a notify hook:

```ini
AT ONBATT * START-TIMER SHUTDOWN 90
AT ONLINE * CANCEL-TIMER SHUTDOWN
```

If power returns within the window, the timer cancels and nothing happens.

---

## 7. Verification

[#7-verification](#7-verification)

On `pi2b`:

```bash
upsc cyberpower@localhost           # live values, ups.status: OL
```

From `swearengen`:

```bash
upsc cyberpower@10.x.x.101
```

From the TrueNAS shell:

```bash
sudo upsc cyberpower@10.x.x.101
```

All three should return the identical live variable dump.

### Reboot test - performed, passed

Rebooted `pi2b` while tailing `journalctl -u nut-monitor -f` on `swearengen`:

```text
Poll UPS [cyberpower@10.x.x.101] failed - Server disconnected
Communications with UPS cyberpower@10.x.x.101 lost
UPS [cyberpower@10.x.x.101]: connect failed: Connection failure: Connection refused
Communications with UPS cyberpower@10.x.x.101 established
```

Total outage window: **~83 seconds**, comfortably inside `DEADTIME 120`.
`SHUTDOWNCMD` never fired; `swearengen` stayed fully up through the entire
cycle. This confirms a normal maintenance reboot of the monitoring host
doesn't threaten the hypervisor.

**Pull-the-plug test — not yet performed.** The reboot test proves the
communication-loss path is safe. It does not prove the actual on-battery /
low-battery shutdown sequence fires correctly end-to-end, or that TrueNAS's
90s timer genuinely beats `swearengen`'s cascade in a real outage. Still
outstanding — see [§8](#8-caveats-and-known-limitations).

### Reading the load

`ups.load` is a percentage of the **watt** rating (1000 W on this model), so
the mental conversion is *percent × 10 = watts*. At time of migration:
~11% ≈ 110 W, 100% charge, ~3825s (~64 min) reported runtime. Sensor
resolution is coarse — treat the number as ±5%.

---

## 8. Caveats and known limitations

[#8-caveats-and-known-limitations](#8-caveats-and-known-limitations)

**The pull-the-plug test hasn't been done.** Communication-loss resilience is
verified; the actual power-loss shutdown cascade is reasoned and configured,
not observed. Configuration that has never fired under real conditions is
still a hypothesis.

**`pi2b` is old, low-spec hardware of unknown history.** BCM2836, 32-bit
ARMv7, found in storage rather than purchased new. It has no other job, which
limits blast radius if it degrades, but it also has no redundancy — if `pi2b`
itself dies, both `swearengen` and TrueNAS lose UPS visibility simultaneously
until it's replaced.

**Single physical USB controller on `pi2b` too, just with nothing else on
it.** The same class of failure that motivated this move is still physically
possible here in principle — the difference is that nothing else competes for
that controller now, so there's no other USB device to trigger it.

**Battery age is unmonitored.** Nothing tracks battery degradation over time.
A NUT exporter feeding Prometheus, with alerting on declining runtime, remains
on the monitoring roadmap.

**TrueNAS's netdata reporting override hardcodes the NUT host address.** That
override was written when `swearengen` was the primary and needs updating to
point at `10.x.x.101` — see
[TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md).

---

## Quick reference

| Need | Command / location |
| --- | --- |
| UPS status (any host) | `upsc cyberpower@10.x.x.101` (or `@localhost` on `pi2b`) |
| Connected clients | `upsc -c cyberpower@localhost` (on `pi2b`) |
| Driver config | `/etc/nut/ups.conf` (on `pi2b`) |
| Which daemons start | `/etc/nut/nut.conf` — `netserver` on `pi2b`, `netclient` elsewhere |
| What `upsd` binds to | `/etc/nut/upsd.conf` — `LISTEN 0.0.0.0 3493` |
| Credentials | `/etc/nut/upsd.users` (on `pi2b`) — one entry per client |
| Monitor config | `/etc/nut/upsmon.conf` |
| Service state | `systemctl status nut-server nut-monitor` / `nut-driver@cyberpower` |
| USB device present | `lsusb` → `0764:0601` |
| USB permissions | `ls -l /dev/bus/usb/001/00N` → `root nut` |
| Fix stuck permissions | `udevadm control --reload-rules && udevadm trigger --subsystem-match=usb` |
| `Driver not connected` from `upsc` | check `nut-driver@cyberpower` status — usually a permissions issue, not a driver bug |
| `ERR ACCESS-DENIED` | credentials differ between `upsd.users` and the client |
| "Connection refused" from a client | no `LISTEN` in `upsd.conf`, or the primary is mid-reboot |
| Live one-liner for a tmux pane | see [Quick monitoring loop](#quick-monitoring-loop) below |

### Quick monitoring loop

Run on `pi2b`, e.g. in a persistent `tmux` session:

```bash
watch -n 10 'echo "== UPS =="; upsc cyberpower@localhost 2>/dev/null | grep -E "^(ups\.status|battery\.charge:|battery\.runtime:|input\.voltage:|ups\.load:)"; echo; echo "== Services =="; for s in nut-driver@cyberpower nut-server nut-monitor; do printf "%-25s %s\n" "$s" "$(systemctl is-active $s)"; done; echo; echo "== Connected Clients =="; ss -tn sport = :3493 | tail -n +2'
```
