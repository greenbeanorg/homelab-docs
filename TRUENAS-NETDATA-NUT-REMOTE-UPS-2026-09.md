# TrueNAS Netdata NUT Collector Failure — Remote UPS
Status: resolved. Root-cause runbook for Netdata UPS monitoring failures on TrueNAS where the shipped `nut_ups` charts.d collector repeatedly attempts to enumerate a local NUT server (`upsc -l`) even though the UPS is configured as a remote NUT endpoint. The standard `nut` collector works correctly with the explicitly configured remote UPS and is now used instead.

* * *

## 1. Symptom

TrueNAS Netdata was not cleanly collecting the configured remote UPS through the `nut_ups` collector.

The UPS itself was healthy and reachable with `upsc`:

    cyberpower@nut.greenbean.org:3493

Direct query succeeded:

    battery.charge: 100
    battery.runtime: 3825
    battery.voltage: 27.4
    input.voltage: 116.0
    output.voltage: 116.0
    ups.load: 11
    ups.status: OL

However, Netdata's `nut_ups.chart.sh` repeatedly logged:

    nut_ups: command 'upsc -l ' failed with code 1:
    Error: Connection failure: Connection refused

This initially appeared to be a NUT connectivity problem, but direct `upsc` testing proved that the remote NUT endpoint was healthy.

* * *

## 2. Environment

TrueNAS host:

    Netdata: v1.37.1

Netdata installation:

    /usr/lib/netdata/
    /etc/netdata/

Relevant collectors:

    /usr/lib/netdata/charts.d/nut.chart.sh
    /usr/lib/netdata/charts.d/nut_ups.chart.sh

NUT endpoint:

    cyberpower@nut.greenbean.org:3493

Netdata service:

    netdata.service

Netdata charts.d plugin:

    /usr/lib/netdata/plugins.d/charts.d.plugin

* * *

## 3. Investigation

### 3.1 Confirmed the UPS itself works

A direct query was performed as the Netdata user.

The equivalent NUT operation:

    upsc cyberpower@nut.greenbean.org:3493

returned valid UPS data including:

    battery.charge
    battery.runtime
    battery.voltage
    input.voltage
    output.voltage
    ups.load
    ups.status

Therefore:

    Network path:        OK
    Remote NUT server:   OK
    Remote UPS:          OK
    NUT protocol:        OK

The failure was isolated to Netdata's collector behavior.

### 3.2 Found the two separate Netdata collectors

TrueNAS ships both:

    nut.chart.sh
    nut_ups.chart.sh

The `charts.d.plugin` loader was capable of running both independently.

Initially both were enabled:

    nut=yes
    nut_ups=yes

This caused both collectors to activate.

### 3.3 Identified the problematic operation

The `nut_ups` collector calls:

    nut_get_all()

which ultimately performs:

    upsc -l

`upsc -l` asks the local/default NUT server to enumerate configured UPSes.

The TrueNAS host was not the NUT server for this UPS. The actual UPS was a remote endpoint:

    cyberpower@nut.greenbean.org:3493

Consequently:

    upsc -l

failed against the local NUT service:

    Error: Connection failure: Connection refused

The collector continued activating but generated unusable chart names such as:

    nut_skip_get_values.charge
    nut_skip_get_values.runtime
    nut_skip_get_values.battery_voltage

This was the key diagnostic distinction.

### 3.4 Tested whether an alias/environment workaround was necessary

A shell alias was considered but rejected.

Netdata's charts.d plugin is launched as a service/plugin process rather than an interactive login shell, so putting an alias in `.shrc` would not be a reliable configuration mechanism.

The collector was instead tested directly with the explicit remote UPS name.

The remote query succeeded with:

    cyberpower@nut.greenbean.org:3493

and returned valid values.

### 3.5 Tested the standard `nut` collector

The configuration was changed to:

    enable_all_charts=no
    nut=yes
    nut_ups=no

The existing NUT configuration was:

    nut_ups="cyberpower@nut.greenbean.org:3493"

After disabling `nut_ups`, the standard `nut` collector successfully created:

    nut_cyberpower_nut_greenbean_org_3493.charge
    nut_cyberpower_nut_greenbean_org_3493.runtime
    nut_cyberpower_nut_greenbean_org_3493.battery_voltage
    nut_cyberpower_nut_greenbean_org_3493.input_voltage
    nut_cyberpower_nut_greenbean_org_3493.input_current
    nut_cyberpower_nut_greenbean_org_3493.input_frequency
    nut_cyberpower_nut_greenbean_org_3493.output_voltage
    nut_cyberpower_nut_greenbean_org_3493.load
    nut_cyberpower_nut_greenbean_org_3493.temp

The collector continued updating at approximately 2-second intervals.

This proved that the standard `nut` collector correctly handles the explicit remote UPS configuration.

* * *

## 4. Fix Applied

### 4.1 Disable the problematic `nut_ups` collector

Created/updated:

    /etc/netdata/charts.d.conf

with:

    enable_all_charts=no
    nut=yes
    nut_ups=no

This prevents `nut_ups.chart.sh` from running while retaining the working `nut.chart.sh` collector.

### 4.2 Preserve the explicit remote UPS configuration

The NUT configuration remains:

    /etc/netdata/charts.d/nut.conf

with:

    nut_ups="cyberpower@nut.greenbean.org:3493"

The somewhat confusing variable name is inherited from the Netdata collector configuration. It is consumed by the standard `nut` collector and is not an indication that the `nut_ups` collector must be enabled.

### 4.3 Remove the experimental collector override

An attempted local copy of:

    /usr/lib/netdata/charts.d/nut_ups.chart.sh

was temporarily placed under:

    /etc/netdata/charts.d/nut_ups.chart.sh

for testing.

That override was removed after determining that it was unnecessary:

    sudo rm -f /etc/netdata/charts.d/nut_ups.chart.sh

No files under `/usr/lib/netdata` were modified.

This is intentional because the TrueNAS base filesystem is protected/immutable and vendor-managed files should not be modified for this workaround.

### 4.4 Restart Netdata

After applying the configuration:

    sudo systemctl restart netdata

Netdata returned to:

    Active: active (running)

The charts.d process was running normally.

* * *

## 5. Verification

### 5.1 Netdata service

    sudo systemctl status netdata --no-pager

Expected:

    Active: active (running)

### 5.2 No NUT errors

    sudo journalctl -u netdata --since "1 minute ago" --no-pager \
      | grep -iE 'nut|error|fail'

Expected:

    no output

### 5.3 Confirm only the standard NUT collector is active

    sudo -u netdata /usr/lib/netdata/plugins.d/charts.d.plugin 2 debug 2>&1 \
      | grep -iE 'nut|upsc|error|fail|BEGIN' \
      | head -80

Expected:

    nut: is enabled for auto-detection.
    nut_ups: is disabled.
    ...
    main: enabled charts: nut
    ...
    nut: module 'nut' activated

Expected chart names include:

    nut_cyberpower_nut_greenbean_org_3493.charge
    nut_cyberpower_nut_greenbean_org_3493.runtime
    nut_cyberpower_nut_greenbean_org_3493.battery_voltage
    nut_cyberpower_nut_greenbean_org_3493.input_voltage
    nut_cyberpower_nut_greenbean_org_3493.output_voltage
    nut_cyberpower_nut_greenbean_org_3493.load

Expected update intervals are approximately:

    2 seconds

No recurring:

    upsc -l
    Connection failure: Connection refused

messages should appear.

* * *

## 6. Root Cause

The immediate root cause is a mismatch between the two Netdata NUT collectors and the topology of the NUT installation.

The UPS is remote:

    TrueNAS
       |
       | NUT client
       |
       +----> nut.greenbean.org:3493
                    |
                    +----> CyberPower UPS

The `nut_ups` collector attempts automatic UPS discovery using:

    upsc -l

That operation targets the local/default NUT server rather than using the explicitly configured remote UPS endpoint.

The local NUT service is not the authoritative server for this UPS, so enumeration fails:

    Connection refused

The standard `nut` collector, however, successfully uses the explicitly configured remote UPS identifier:

    cyberpower@nut.greenbean.org:3493

Therefore the UPS and NUT network connection were never the problem.

* * *

## 7. Why We Did Not Patch `/usr/lib`

The original attempt to back up:

    /usr/lib/netdata/charts.d/nut_ups.chart.sh

failed with:

    cp: cannot create regular file ... Read-only file system

This is expected on the TrueNAS appliance-style base filesystem.

Do not work around this by modifying or remounting the system filesystem merely to patch Netdata.

Vendor-managed files under:

    /usr/lib/netdata/

may be replaced by TrueNAS updates and are not an appropriate location for a persistent local workaround.

The working configuration is entirely under:

    /etc/netdata/

* * *

## 8. Upstream / TrueNAS Status

The installed Netdata version is:

    v1.37.1

This is an old Netdata release.

The TrueNAS middleware source continues to integrate Netdata configuration and NUT-related configuration into the operating system. The current TrueNAS source also contains Netdata UPS-related configuration under its middleware-generated configuration tree.

As of this investigation, no confirmed TrueNAS patch specifically fixing the `nut_ups` remote-UPS behavior was identified.

Therefore this runbook treats the configuration change as a local operational workaround rather than an upstream bug fix.

Future TrueNAS/Netdata upgrades should be checked to determine whether the obsolete collector behavior has changed.

* * *

## 9. Known Limitations

- `nut_ups.chart.sh` remains installed in the TrueNAS base filesystem but is deliberately disabled.
- A future TrueNAS update may change Netdata's charts.d implementation or configuration semantics.
- The installed Netdata version (1.37.1) is substantially older than current upstream Netdata releases.
- The configuration uses the legacy charts.d NUT collector rather than a newer Netdata collector architecture.
- The variable name `nut_ups` in `nut.conf` can be confusing because the working collector is `nut`, not `nut_ups`.
- If the remote NUT server becomes unavailable, the Netdata UPS charts will naturally stop receiving valid values; this is separate from the collector bug described here.

* * *

## 10. Rollback

To restore the previous collector configuration:

    sudo tee /etc/netdata/charts.d.conf >/dev/null <<'EOF'
    enable_all_charts=no
    nut=yes
    nut_ups=yes
    EOF

Then:

    sudo systemctl restart netdata

However, this is **not recommended** for the current remote-UPS topology because it restores the `upsc -l` failure behavior.

The preferred configuration is:

    enable_all_charts=no
    nut=yes
    nut_ups=no

* * *

## 11. Quick Reference

| Check | Command |
|---|---|
| Netdata status | `systemctl status netdata --no-pager` |
| Netdata recent NUT errors | `journalctl -u netdata --since "1 minute ago" --no-pager \| grep -iE 'nut|error|fail'` |
| Verify NUT collector | `sudo -u netdata /usr/lib/netdata/plugins.d/charts.d.plugin 2 debug` |
| UPS direct query | `upsc cyberpower@nut.greenbean.org:3493` |
| Netdata configuration | `cat /etc/netdata/charts.d.conf` |
| NUT charts.d configuration | `cat /etc/netdata/charts.d/nut.conf` |
| Installed Netdata version | `/usr/sbin/netdata -W buildinfo` |
| Check collector files | `ls -l /usr/lib/netdata/charts.d/nut*` |

* * *

## 12. Final State

The final working configuration is:

    /etc/netdata/charts.d.conf

    enable_all_charts=no
    nut=yes
    nut_ups=no

and:

    /etc/netdata/charts.d/nut.conf

    nut_ups="cyberpower@nut.greenbean.org:3493"

The resulting data path is:

    CyberPower UPS
          |
          v
    nut.greenbean.org:3493
          |
          v
    Netdata `nut.chart.sh`
          |
          v
    UPS charts

The broken path is intentionally disabled:

    Netdata `nut_ups.chart.sh`
          |
          v
       `upsc -l`
          |
          v
    local NUT socket
          |
          v
    Connection refused

**Resolution:** disable `nut_ups`; use the standard `nut` collector with the explicit remote UPS endpoint.

* * *
