# UPS — CyberPower Monitoring via NUT

UPS monitoring for the lab, converted from CyberPower's vendor tooling
(PowerPanel / `pwrstat`) to **NUT** in a netserver/netclient topology.

The Proxmox host owns the USB connection and serves UPS state over the network;
the TrueNAS SCALE VM consumes it as a client. Nothing else on the host tree
talks to the UPS directly.

- **NUT master:** `swearengen` — Proxmox VE 9.2, USB to the UPS — **10.x.x.5**
- **NUT client:** TrueNAS SCALE VM (netclient / "Slave") — **10.x.x.20**
- **UPS:** CyberPower CP1500PFCLCDa — 1500 VA / 1000 W
- **Driver:** `usbhid-ups`
- **Rollout:** `swearengen` complete; remaining `pwrstat` hosts outstanding — see [§8](#8-caveats-and-known-limitations)
- **Date:** August 2026

| Question | Document |
| --- | --- |
| **NUT topology, shutdown coordination, host config** | **this document** |
| Why the TrueNAS **Reporting → UPS** page is blank | [TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md) |

> The TrueNAS reporting graphs are broken by an upstream defect (NAS-132924) and
> have their own runbook. Shutdown coordination — the part that protects the
> pool — is unaffected and is what this document covers.

---

## 1. Design

[#1-design](#1-design)

```text
CyberPower UPS (USB)
        │
        ▼
swearengen — Proxmox host          10.x.x.5
   ├── usbhid-ups  (driver)
   ├── upsd        (serves state on :3493)
   ├── upsmon      (primary)
   │
   └──── network ────► TrueNAS SCALE VM       10.x.x.20
                         upsmon (netclient)
```

**The hypervisor owns the USB device, not the VM.** Passing the UPS through to
TrueNAS would mean the machine that has to shut down *last* is the only one that
knows power was lost. Proxmox needs that signal first so it can bring guests
down in order before powering itself off.

**Everything else is a network client.** TrueNAS receives UPS state over
`:3493` rather than owning hardware. Same signal, no device contention, and any
future host can subscribe without touching the UPS.

**A guest cannot shut down its own hypervisor.** This is the reason the design
isn't reversible — it's the constraint, not a preference.

### Shutdown ordering

| Stage | Trigger | Why |
| --- | --- | --- |
| TrueNAS VM | on-battery, ~296s timer | Exports the ZFS pool cleanly well before power runs out |
| Other guests | Proxmox shutdown sequence | Ordinary guest shutdown |
| `swearengen` | `battery.charge.low` / runtime low (300s) | Last out, after storage is quiesced |

The VM's timer sits deliberately *below* the host's low-battery threshold so
storage is always down first. At the measured load (~15%, roughly 150 W of the
UPS's 1000 W rating) reported runtime is ~3200s, so the margin is large — the
ordering matters more than the timings.

---

## 2. Install and confirm detection

[#2-install-and-confirm-detection](#2-install-and-confirm-detection)

```bash
apt update
apt install nut
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
maxretry = 3

[cyberpower]
    driver = usbhid-ups
    port = auto
    desc = "CyberPower CP1500PFCLCDa"
```

### USB permissions

The `nut` user must own the device node or `usbhid-ups` fails to open it:

```bash
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb
ls -l /dev/bus/usb/001/002        # expect: root nut
```

Test the driver before going further:

```bash
upsdrvctl start
upsc cyberpower
```

Expect battery charge, runtime, load, input voltage, and `ups.status: OL`.

---

## 4. Server

[#4-server](#4-server)

⚠️ **`MODE` does not control what `upsd` binds to.** It only selects which
daemons the init scripts start. Listening is governed entirely by `LISTEN`
directives in `upsd.conf`, and with no `LISTEN` line at all the default is
loopback only — which is the usual reason a netclient "can't connect" while
`upsc` works fine on the host itself.

`/etc/nut/nut.conf` — as running here:

```ini
MODE=standalone
```

`/etc/nut/upsd.conf` — this is what actually makes the client work:

```ini
LISTEN 0.0.0.0 3493
```

The pairing is worth understanding rather than copying: `standalone` nominally
describes a host with no network clients, but the explicit `LISTEN` overrides
that and serves the TrueNAS VM regardless. Upstream's convention for this
topology is `MODE=netserver`; the running config predates that distinction and
works, so it is documented as-is rather than quietly "corrected". See
[§8](#8-caveats-and-known-limitations).

`/etc/nut/upsd.users`:

```ini
[monuser]
    password = ${UPS_MONUSER_PASSWORD}
    upsmon primary
```

The same credentials are entered on the TrueNAS side in [§6](#6-truenas-as-a-netclient);
a mismatch surfaces as `ERR ACCESS-DENIED` rather than a connection failure.

---

## 5. Monitor

[#5-monitor](#5-monitor)

`/etc/nut/upsmon.conf`:

```ini
MONITOR cyberpower@localhost 1 monuser ${UPS_MONUSER_PASSWORD} primary
```

Start and enable:

```bash
systemctl enable --now nut-server nut-monitor
systemctl status nut-server nut-monitor
```

---

## 6. TrueNAS as a netclient

[#6-truenas-as-a-netclient](#6-truenas-as-a-netclient)

**System Settings → Services → UPS**, configured through the UI rather than by
hand-editing `/etc/nut/` inside the VM:

| Field | Value |
| --- | --- |
| UPS Mode | Slave |
| Remote Host | `10.x.x.5` |
| Remote Port | `3493` |
| Identifier | `cyberpower` |
| Monitor User | `monuser` |
| Monitor Password | as set in `upsd.users` |
| Shutdown Mode | UPS goes on battery |
| Shutdown Timer | `296` |

⚠️ **Configure this in the UI, not by editing files in the VM.** TrueNAS
middleware regenerates NUT configuration; hand edits are reverted on update, and
the middleware also won't know the service exists — which is a separate failure
described in [TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md).

---

## 7. Verification

[#7-verification](#7-verification)

On the master:

```bash
upsc cyberpower                   # live values, ups.status: OL
upsc -c cyberpower                # connected clients — expect 127.0.0.1 and 10.x.x.20
```

From the TrueNAS shell:

```bash
sudo upsc cyberpower@10.x.x.5     # full variable dump over the network
```

`upsc -c` listing the VM's address is the definitive proof that netserver mode
and the LISTEN directive are correct — it is the check that distinguishes a
working client from a working *local* driver.

**Pull-the-plug test.** Not optional, and not yet performed on this deployment.
Verify the on-battery event fires, the TrueNAS timer expires first, the pool
exports cleanly, and the host follows.

### Reading the load

`ups.load` is a percentage of the **watt** rating, not VA. On the CP1500PFCLCDa
that is 1000 W, so the mental conversion is *percent × 10 = watts*: 15% ≈ 150 W.
Sensor resolution is coarse — treat the number as ±5%.

---

## 8. Caveats and known limitations

[#8-caveats-and-known-limitations](#8-caveats-and-known-limitations)

**The conversion is incomplete.** `swearengen` is done; other hosts still run
`pwrstat` against their own UPSes with no shared state and no coordinated
shutdown. Until those are converted, a UPS that isn't this one has no ordering
guarantees at all.

**No coordinated shutdown beyond the master.** Other hosts on this UPS are not
yet netclients, so only `swearengen` and the TrueNAS VM participate in ordered
shutdown.

**The shutdown path is untested under real power loss.** Timers are configured
and the ordering is reasoned, but nothing has been verified by actually pulling
power. Configuration that has never fired is a hypothesis.

**Reporting is broken upstream.** The TrueNAS graphs require a patched netdata
module that must be re-applied after every OS upgrade — see
[TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md). Monitoring here is
functional but not observable through the appliance UI.

**`MODE=standalone` is semantically wrong for this topology.** It works only
because `upsd.conf` carries an explicit `LISTEN`. Anyone reading `nut.conf`
alone would reasonably conclude there are no network clients. Switching to
`MODE=netserver` is the accurate label and changes no behaviour, but it does
restart the daemons — schedule it rather than doing it casually on the host that
owns storage.

**`LISTEN 0.0.0.0` binds every interface.** On a Proxmox host that includes
every bridge and any future VLAN interface, not just the LAN address. Access is
gated only by the `monuser` credential. Narrowing to `LISTEN 10.x.x.5 3493` is
the tighter configuration and costs nothing — worth doing alongside the VLAN
segmentation work, when interface count goes up.

**Battery age is unmonitored.** Nothing tracks battery degradation. A NUT
exporter feeding Prometheus, with alerting on declining runtime, is the standing
fix and is on the monitoring roadmap.

---

## Quick reference

| Need | Command / location |
| --- | --- |
| UPS status | `upsc cyberpower` |
| Connected clients | `upsc -c cyberpower` (expect `10.x.x.20`) |
| Read from the netclient | `sudo upsc cyberpower@10.x.x.5` |
| Current draw in watts | `upsc cyberpower ups.load` × 10 (1000 W rating) |
| Driver config | `/etc/nut/ups.conf` |
| Which daemons start | `/etc/nut/nut.conf` (`MODE=standalone` here) |
| What `upsd` binds to | `/etc/nut/upsd.conf` (`LISTEN 0.0.0.0 3493`) |
| Credentials | `/etc/nut/upsd.users` |
| Monitor config | `/etc/nut/upsmon.conf` |
| Service state | `systemctl status nut-server nut-monitor nut-driver` |
| USB device present | `lsusb` → `0764:0601` |
| USB permissions | `ls -l /dev/bus/usb/001/002` → `root nut` |
| `ERR ACCESS-DENIED` | credentials differ between `upsd.users` and the client |
| "Connection refused" from a client | no `LISTEN` in `upsd.conf`, or it binds loopback only |
| Blank TrueNAS graphs | [TRUENAS-UPS-REPORTING.md](TRUENAS-UPS-REPORTING.md) |
