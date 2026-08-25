# TrueNAS SCALE: Blank UPS Reporting Graphs in Netclient (Slave) Mode

**Status:** Resolved with community workaround — must survive OS upgrades (see persistence section)
**Affects:** TrueNAS SCALE 24.x – 25.10.x (confirmed still present in 25.10.3/25.10.4 as of June 2026)
**Upstream:** [NAS-132924](https://ixsystems.atlassian.net/browse/NAS-132924) · [forum thread](https://forums.truenas.com/t/scale-remote-ups-data-not-showing-on-reports/11521)

## Problem

TrueNAS SCALE runs as a NUT **netclient (Slave)** against a NUT master on another host
(here: Proxmox host `swearengen`, upsd serving a CyberPower CP1500PFCLCDa). The UPS
*connection* works perfectly — shutdown coordination is functional — but the
**Reporting → UPS page renders every graph empty**, forever.

## Symptom chain (how this was diagnosed)

Each check below passed or came back empty, which is what isolates the bug:

```
# 1. NUT master side: TrueNAS shows up as a connected client → transport OK
root@swearengen:~# upsc -c cyberpower
10.x.x.20        ← the TrueNAS VM
127.0.0.1

# 2. TrueNAS side: full variable dump from the master → NUT client OK
aba@truenas ~ % sudo upsc cyberpower@10.x.x.5
battery.charge: 100
ups.status: OL
... (full output)

# 3. But netdata has NO upsd collector configured, and isn't even failing:
aba@truenas ~ % sudo grep -ri "upsd\|3493\|10.x.x.5" /etc/netdata/
(empty)
aba@truenas ~ % sudo journalctl -u netdata --since "-10 min" | grep -i upsd
(empty)
```

⚠️ **The empty greps are the tell.** UPS charts on SCALE are not driven by netdata's
go.d `upsd` collector (hence nothing in `/etc/netdata/` or the journal). They come from
a legacy charts.d shell module: `/usr/lib/netdata/charts.d/nut_ups.chart.sh`.

## Root cause

The `nut_ups.chart.sh` module calls `upsc -l` **with no host argument** — it assumes a
local upsd. In netclient/Slave mode there is no local upsd, so every poll fails, no
charts are ever created, and the failures spam `/var/log/netdata/error.log`
(worth checking the size of that file — this bug generates roughly one error per second).

## Fix

Credit: Neil MacLeod (MilhouseVH), from the NAS-132924 ticket comments. charts.d modules
source an optional config file from `/etc/netdata/charts.d/`, which lets us **override the
two broken functions without touching the immutable root filesystem** — no
`/usr/lib` edits, 100% reversible (delete the file, restart netdata).

Create `/etc/netdata/charts.d/nut_ups.conf`:

```bash
# Configure remote UPS address:port here (NUT master)
MY_NUT_HOST="10.x.x.5:3493"

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

Then:

```bash
systemctl restart netdata.service
```

Result: functioning Slave UPS graphs, and the error.log spam stops.

## Persistence across upgrades

⚠️ TrueNAS OS upgrades wipe the override, so the fix must be re-applied after every
update. To automate it, keep a master copy at `/root/bin/nut_ups.conf` and register the
following as an init script (System → Advanced Settings → Init/Shutdown Scripts):

```bash
#!/bin/bash
#
# Run after an OS upgrade
#
# Fix netdata SLAVE UPS bug (NAS-132924)
if [ -f /usr/lib/netdata/charts.d/nut_ups.chart.sh \
     -a -d /etc/netdata/charts.d \
     -a ! -f /etc/netdata/charts.d/nut_ups.conf ]; then
  cp /root/bin/nut_ups.conf /etc/netdata/charts.d/
  pgrep netdata >/dev/null && systemctl restart netdata || true
fi
```

The guard conditions make it a no-op when the fix is already in place, and it
self-heals on the first boot after any upgrade that removed it.

Remove the override once an upstream fix ships (watch NAS-132924).

## Verification

- Reporting → UPS graphs populate within a few minutes of the netdata restart
  (data exists from restart-forward only — zoom the chart window to "now")
- `/var/log/netdata/error.log` no longer accumulates nut_ups failures
- After the next TrueNAS update: confirm `/etc/netdata/charts.d/nut_ups.conf`
  exists and graphs still populate (proves the init script did its job)

## Design note

The strategic fix is not to depend on appliance charts at all: a Prometheus NUT
exporter polling upsd on the master will provide fleet-wide UPS dashboards in
Grafana for **all** UPSes, not just what one appliance renders. This workaround
is a bridge until that monitoring stack lands. Shutdown coordination — the part
that protects the pool — never depended on any of this and worked throughout.
