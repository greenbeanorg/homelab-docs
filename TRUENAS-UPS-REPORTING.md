# TRUENAS-UPS-REPORTING — Blank UPS Graphs in Netclient Mode

Why the TrueNAS SCALE **Reporting → UPS** page renders empty when the box is a NUT
*netclient*, and the config override that fixes it without touching the immutable root
filesystem.

The UPS connection itself was never broken. Shutdown coordination worked throughout — this
document is entirely about the reporting path, which is a separate mechanism with a
separate failure.

- **Host:** TrueNAS SCALE VM on `swearengen`, NUT netclient (Slave)
- **NUT primary:** originally `swearengen` at `10.x.x.5` — **moved to `pi2b` at `10.x.x.101` as of September 2026**, see [UPS.md](UPS.md)
- **Affects:** TrueNAS SCALE 24.x – 25.10.x (still present in 25.10.3 / 25.10.4, June 2026)

> ⚠️ **Action needed:** the fix below hardcodes `MY_NUT_HOST` to the old
> `swearengen` address. If this override is live in production, it needs
> updating to the new primary's address or TrueNAS's UPS reporting graphs go
> blank again — not from the original NAS-132924 bug recurring, but from
> pointing at a host that no longer runs `upsd`. Check
> `/etc/netdata/charts.d/nut_ups.conf` and the init-script master copy at
> `/root/bin/nut_ups.conf` ([§5](#5-surviving-upgrades)) for the stale value.
- **Upstream:** [NAS-132924](https://ixsystems.atlassian.net/browse/NAS-132924)
- **Prerequisite:** [UPS.md](UPS.md) — the pwrstat → NUT conversion this sits on top of
- **Date:** August 2026

| Question | Document |
| --- | --- |
| Why NUT, netserver/netclient topology, shutdown ordering | [UPS.md](UPS.md) |
| **Why the TrueNAS reporting page is blank, and the fix** | **this document** |

---

## 1. Symptom

Every chart on Reporting → UPS is empty, permanently. Auto-refresh is on, the time window
is valid, and the UPS service shows as running.

---

## 2. Diagnosis

Each check below either passed or returned nothing, and it's the *combination* that
isolates the fault.

**NUT master — the client is connected:**

```
root@swearengen:~# upsc -c cyberpower
10.x.x.20        ← the TrueNAS VM
127.0.0.1
```

**TrueNAS — full variable dump over the network:**

```
aba@truenas ~ % sudo upsc cyberpower@10.x.x.5
battery.charge: 100
ups.status: OL
... (complete output)
```

Transport and authentication are fine. But netdata has no UPS collector at all, and is not
even failing:

```
aba@truenas ~ % sudo grep -ri "upsd\|3493\|10.x.x.5" /etc/netdata/
(empty)
aba@truenas ~ % sudo journalctl -u netdata --since "-10 min" | grep -i upsd
(empty)
```

⚠️ **The empty greps are the finding.** UPS charts on SCALE do not come from netdata's
go.d `upsd` collector — which is why nothing appears under `/etc/netdata/` or in the
journal. They come from a legacy charts.d shell module at
`/usr/lib/netdata/charts.d/nut_ups.chart.sh`.

⚠️ **Beware `zpool events` style cross-talk.** An early false lead was a scrub event that
turned out to belong to `boot-pool`, not `tank`. Check which subsystem an event actually
belongs to before building a theory on it.

---

## 3. Root cause

`nut_ups.chart.sh` calls `upsc -l` **with no host argument**, assuming a local `upsd`. In
netclient mode there is no local `upsd`, so every poll fails, no charts are ever created,
and the failures land in `/var/log/netdata/error.log` at roughly one per second.

Check that file's size on a long-uptime box — this bug is a quiet disk consumer.

---

## 4. Fix

Credit: Neil MacLeod (MilhouseVH), from the NAS-132924 comments.

charts.d modules source an optional config from `/etc/netdata/charts.d/`, which allows
overriding the two broken functions **without editing `/usr/lib`** — no immutable-rootfs
workaround needed, and fully reversible by deleting the file.

`/etc/netdata/charts.d/nut_ups.conf`:

```bash
# NUT primary (updated Sept 2026 — was 10.x.x.5 when swearengen held the UPS)
MY_NUT_HOST="10.x.x.101:3493"

nut_get_all() {
  run -t $nut_timeout upsc -l ${MY_NUT_HOST} || echo "skip-get-values"
}

nut_get() {
  if [ $1 == "skip-get-values" ]; then
    return 0;
  fi

  run -t $nut_timeout upsc "${1}@${MY_NUT_HOST}"

  if [ "${nut_clients_chart}" -eq "1" ]; then
    printf "ups.connected_clients: "
    run -t $nut_timeout upsc -c "${1}@${MY_NUT_HOST}" | wc -l
  fi
}
```

```bash
systemctl restart netdata.service
```

Charts populate within a few minutes, and the error.log spam stops.

---

## 5. Surviving upgrades

⚠️ TrueNAS OS upgrades wipe the override. Keep a master copy at `/root/bin/nut_ups.conf`
and register this as an init script (System → Advanced Settings → Init/Shutdown Scripts):

```bash
#!/bin/bash
#
# Fix netdata SLAVE UPS bug (NAS-132924) after an OS upgrade
#
if [ -f /usr/lib/netdata/charts.d/nut_ups.chart.sh \
     -a -d /etc/netdata/charts.d \
     -a ! -f /etc/netdata/charts.d/nut_ups.conf ]; then
  cp /root/bin/nut_ups.conf /etc/netdata/charts.d/
  pgrep netdata >/dev/null && systemctl restart netdata || true
fi
```

The guard conditions make it a no-op when the fix is already in place, so it self-heals on
the first boot after any upgrade that removed it.

---

## 6. Verification

- Reporting → UPS populates within a few minutes of the netdata restart. Data exists only
  from the restart forward — zoom the chart window to *now* rather than judging an empty
  historical range.
- `/var/log/netdata/error.log` stops accumulating `nut_ups` failures.
- **After the next TrueNAS update:** confirm `/etc/netdata/charts.d/nut_ups.conf` still
  exists and charts still populate. This is the only real test of the init script, and the
  step most likely to be forgotten.

---

## 7. Caveats and known limitations

- **This is a patched appliance, not a fixed one.** The override has to survive every
  upgrade via the init script. Remove it once an upstream fix ships — watch NAS-132924.
- **Single point of configuration.** `MY_NUT_HOST` is hardcoded. If the NUT master moves or
  is renumbered, this file needs editing by hand along with everything else. This already
  happened once — the primary moved from `swearengen` to `pi2b` in September 2026 — and is
  exactly the kind of edit that's easy to forget when the visible symptom (blank graphs)
  looks identical to the original NAS-132924 bug this document exists to fix.
- **Appliance charts are a dead end long-term.** A Prometheus NUT exporter polling `upsd`
  on the master gives fleet-wide UPS metrics in Grafana for *every* UPS, with none of the
  survives-updates fragility. This workaround is a bridge to that, not a destination.
- **Nothing here protects the pool.** Shutdown coordination is [UPS.md](UPS.md)'s job and
  was never affected by this bug.

---

## Quick reference

| Need | Command / location |
| --- | --- |
| Confirm master sees the client | `upsc -c cyberpower` (on the NUT master) |
| Confirm netclient can read the UPS | `sudo upsc cyberpower@10.x.x.5` |
| The broken module | `/usr/lib/netdata/charts.d/nut_ups.chart.sh` |
| The override | `/etc/netdata/charts.d/nut_ups.conf` |
| Master copy for upgrades | `/root/bin/nut_ups.conf` |
| Apply after editing | `systemctl restart netdata.service` |
| Where failures are logged | `/var/log/netdata/error.log` (not journald) |
| Revert entirely | delete the override, restart netdata |
| Upstream ticket | NAS-132924 |
