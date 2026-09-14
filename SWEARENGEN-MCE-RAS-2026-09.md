# swearengen Memory / MCE / RAS Investigation — 2026-09

Status: closed / no hardware error confirmed. Follow-up testing documented for future recurrence.

Investigation performed: 2026-09-14

Diagnostic runbook for `swearengen`, the primary Proxmox host, covering investigation of possible
memory instability, Machine Check Exceptions (MCE), EDAC/RAS reporting, and PCIe AER errors.

No definitive DRAM, CPU, PCIe, or other hardware error was identified during this investigation.

The host has 48 GB of installed system memory.

---

## Contents

1. [Symptom / Investigation Trigger](#1-symptom--investigation-trigger)
2. [Result](#2-result)
3. [Hardware / Memory Configuration](#3-hardware--memory-configuration)
4. [RAS / rasdaemon Investigation](#4-ras--rasdaemon-investigation)
5. [Kernel Trace Configuration](#5-kernel-trace-configuration)
6. [MCE Capability / Machine Check State](#6-mce-capability--machine-check-state)
7. [EDAC Investigation](#7-edac-investigation)
8. [rasdaemon Tracepoint Messages](#8-rasdaemon-tracepoint-messages)
9. [PCIe Devices](#9-pcie-devices)
10. [Relevant Log Search](#10-relevant-log-search)
11. [What Was Ruled Out](#11-what-was-ruled-out)
12. [Current Assessment](#12-current-assessment)
13. [Recommended Future Testing](#13-recommended-future-testing)
14. [Recommended Diagnostic Policy](#14-recommended-diagnostic-policy)
15. [Known Limitations](#15-known-limitations)
16. [Quick Reference](#16-quick-reference)
17. [Final Disposition](#17-final-disposition)

---

## 1. Symptom / Investigation Trigger

> ⚠️ **TODO:** record the specific symptom that prompted this investigation (crash, VM stall,
> unexpected reboot, log message noticed, etc.) and the approximate time it occurred. The rest of
> this document explains the methodology exhaustively but not what precipitated it — that detail
> is what future-you will actually search for first.

Investigation was performed to determine whether the host had evidence of:

- DRAM/memory instability
- CPU Machine Check Exceptions (MCE)
- EDAC-reported corrected or uncorrected memory errors
- Linux `memory_failure` events
- PCIe Advanced Error Reporting (AER) errors
- Other RAS-reported hardware errors
- Possible memory configuration / XMP-related instability

The investigation was intended to determine whether the memory subsystem could be
implicated in host instability.

---

## 2. Result

### Conclusion

No hardware error was captured by the Linux RAS/MCE infrastructure during the investigation.

`ras-mc-ctl --errors` reported:

- No Memory errors
- No PCIe AER errors
- No ARM processor errors
- No CXL errors
- No Extlog errors
- No devlink errors
- No disk errors
- No Memory failure errors
- No MCE errors

The corresponding `ras-mc-ctl --summary` output was also completely clean.

### ⚠️ Important qualification

**A clean RAS/MCE database does not prove that the memory subsystem is perfect.**

This is a consumer Intel Comet Lake-S / Z490 platform and the installed memory is not
a homogeneous server DIMM configuration. EDAC memory-controller reporting was not
registered for this system, so DIMM-level corrected-error telemetry is limited.

Therefore:

> No memory hardware failure was demonstrated, but intermittent memory instability
> cannot be completely ruled out by the evidence collected.

---

## 3. Hardware / Memory Configuration

### Platform

PCI identification:

    00:00.0 Host bridge [0600]:
    Intel Corporation Comet Lake-S 6c Host Bridge/DRAM Controller
    [8086:9b53] (rev 05)

Chipset:

    Intel Z490

CPU platform:

    Intel Comet Lake-S

Logical CPUs:

    12

### Installed memory

Total installed memory:

    48 GB

`dmidecode` reports four populated DIMM locations:

| Slot | Part Number | Rated Speed | Configured/Reported Speed | Rank |
|---|---|---:|---:|---:|
| ChannelA-DIMM1 | CMK32GX4M2B3000C15 | 3000 MT/s | 3000 MT/s | 2 |
| ChannelA-DIMM2 | CMK16GX4M2B3200C16 | 3200 MT/s | 3000 MT/s | 1 |
| ChannelB-DIMM1 | CMK32GX4M2B3000C15 | 3000 MT/s | 3000 MT/s | 2 |
| ChannelB-DIMM2 | CMK16GX4M2B3200C16 | 3200 MT/s | 3000 MT/s | 1 |

The configuration therefore contains mixed Corsair memory part numbers / kits, mixed rated
speeds (3000 vs 3200), and mixed rank characteristics. The 3200-rated pair is currently running
below its rated speed to match the 3000-rated pair — the "Rated" vs "Configured" columns above
make that underclock visible directly in the inventory rather than only in prose.

This is not proof of instability, but it is a relevant variable if unexplained
memory-related crashes recur.

### ⚠️ Important interpretation note

The strings `CMK32GX4M2...` and `CMK16GX4M2...` describe Corsair kit/product
identifiers and should not be interpreted as four individual DIMMs of 32/16 GB
respectively.

The system's actual installed RAM is 48 GB.

---

## 4. RAS / rasdaemon Investigation

The rasdaemon database exists and contains the expected event tables:

    aer_event
    arm_event
    cxl_aer_ce_event
    cxl_aer_ue_event
    cxl_dram_event
    cxl_general_media_event
    cxl_generic_event
    cxl_memory_module_event
    cxl_overflow_event
    cxl_poison_event
    devlink_event
    disk_errors
    extlog_event
    memory_failure_event
    mc_event
    mce_record
    non_standard_event

Additional platform-specific tables are also present.

Database location:

    /var/lib/rasdaemon/ras-mc_event.db

### Current error state

    ras-mc-ctl --errors

Result:

    No Memory errors.
    No PCIe AER errors.
    No ARM processor errors.
    No CXL AER uncorrectable errors.
    No CXL AER correctable errors.
    No CXL overflow errors.
    No CXL poison errors.
    No CXL generic errors.
    No CXL general media errors.
    No CXL DRAM errors.
    No CXL memory module errors.
    No Extlog errors.
    No devlink errors.
    No disk errors.
    No Memory failure errors.
    No MCE errors.

`ras-mc-ctl --summary` produced the same clean result.

---

## 5. Kernel Trace Configuration

Tracing was explicitly enabled for relevant RAS/MCE events.

    /sys/kernel/debug/tracing/tracing_on
        1

    /sys/kernel/debug/tracing/events/mce/mce_record/enable
        1

    /sys/kernel/debug/tracing/events/ras/mc_event/enable
        1

    /sys/kernel/debug/tracing/events/ras/aer_event/enable
        1

This means the relevant tracepoints were enabled during the investigation.

---

## 6. MCE Capability / Machine Check State

The `msr` kernel module was loaded and the Machine Check MSRs were readable.

    modprobe msr

### IA32_MCG_CAP (MSR 0x179)

    rdmsr -a 0x179

Result on every logical CPU:

    c0c

Decoded: bits [7:0] of `IA32_MCG_CAP` give the machine-check bank count. `0xc0c` = `0000 1100
0000 1100` — the low byte is `0x0c` = 12, matching the 12 machine-check banks referenced below.
The higher bits set (`0xc00`) indicate `MCG_SER_P` and `MCG_EXT_CNT`-related capability flags are
present; they don't change the bank count and aren't otherwise relevant here.

This indicates the processor exposes 12 machine-check banks.

### IA32_MCG_CTL (MSR 0x17a)

    rdmsr -a 0x17a

Result on every logical CPU:

    0

No global machine-check control mask was observed disabling the MCE mechanism.

### Machine-check sysfs

The kernel exposes:

    /sys/devices/system/machinecheck/machinecheck0
    ...
    /sys/devices/system/machinecheck/machinecheck11

Each machine-check instance exposes the expected bank controls.

Relevant state includes:

    ignore_ce: 0
    dont_log_ce: 0
    cmci_disabled: 0
    print_all: 0
    check_interval: 300

Corrected errors are therefore not intentionally being ignored by the visible
machine-check configuration.

---

## 7. EDAC Investigation

Available EDAC modules include:

    /lib/modules/7.0.14-15-pve/kernel/drivers/edac/edac_mce_amd.ko
    /lib/modules/7.0.14-15-pve/kernel/drivers/edac/i10nm_edac.ko
    /lib/modules/7.0.14-15-pve/kernel/drivers/edac/skx_edac_common.ko
    /lib/modules/7.0.14-15-pve/kernel/drivers/edac/skx_edac.ko

### ⚠️ Important

The presence of these modules in the kernel module directory does not mean they
apply to this particular motherboard/CPU.

The platform identifies as:

    Intel Comet Lake-S
    PCI ID 8086:9b53

`edac_mce_amd` targets AMD platforms, and `i10nm_edac`/`skx_edac` target Intel Ice Lake-SP and
Skylake-X **server** memory controllers. None of the four target Comet Lake-S desktop silicon —
this isn't a "wrong driver version" situation to troubleshoot, it's that mainline Linux generally
has no in-kernel EDAC memory-controller driver for this platform's integrated memory controller.
The `i10nm` and `skx` EDAC drivers should therefore not be treated as the
appropriate Comet Lake-S memory-controller driver merely because their `.ko`
files exist, and time shouldn't be spent trying to force one to bind.

### EDAC kernel state

Kernel log:

    EDAC MC: Ver: 3.0.0

However:

    /sys/devices/system/edac/mc/

does not contain an actual `mc0`, `mc1`, etc. memory controller device.

Only the generic EDAC subsystem directory and power-management files are present.

Therefore there is no DIMM-level EDAC memory-controller telemetry currently
available from this host.

---

## 8. rasdaemon Tracepoint Messages

The rasdaemon service logged temporary failures while creating/accessing some
tracepoint instances:

    wait_access failed, .../ras/memory_failure_event/format not created in 30000 ms

    Can't get ras:memory_failure_event traces: Operation not permitted

Similar messages appeared for:

    cxl:cxl_dram

    cxl:memory_module

These were followed by successful recording messages such as:

    Recording memory_failure_event events
    Recording cxl_dram_event events
    Recording cxl_memory_module_event events

These messages appear to be related to rasdaemon's trace-instance setup/access
behavior rather than evidence that a memory error occurred.

They should not be interpreted as actual DRAM errors.

---

## 9. PCIe Devices

Relevant PCI devices:

    01:00.0 VGA compatible controller:
    AMD/ATI Hawaii PRO [Radeon R9 290/390]

    02:00.0 Non-Volatile memory controller:
    Samsung PM9A1/PM9A3/980PRO

    04:00.0 Ethernet controller:
    Intel 82575EB Gigabit Network Connection

    04:00.1 Ethernet controller:
    Intel 82575EB Gigabit Network Connection

PCIe AER reporting was clean:

    No PCIe AER errors.

No evidence was found during this investigation that PCIe errors were contributing
to the suspected instability.

---

## 10. Relevant Log Search

A system log search was performed for:

    xmp
    memory
    dram
    overclock

The results contained routine systemd memory accounting and rasdaemon messages,
but no clear evidence of:

- XMP errors
- DRAM ECC errors
- MCEs
- WHEA hardware errors
- kernel memory failures
- explicit overclock failures

The rasdaemon messages concerning tracepoint access are documented separately
above.

As a fallback for future recurrence, also check `dmesg` directly rather than relying solely on
`journalctl -k`, since the ring buffer and the journal can diverge and `dmesg` may still hold an
event that has rotated out of the persisted journal:

    dmesg | grep -iE 'mce|edac|whea|hardware error'

---

## 11. What Was Ruled Out

### Confirmed absent

The following were not observed in the available RAS/MCE data:

- Corrected memory errors
- Uncorrected memory errors
- MCE records
- Linux memory-failure events
- PCIe AER errors
- CXL memory errors
- Extlog hardware errors
- Disk errors reported through rasdaemon

### Not conclusively ruled out

The following remain possible if the system experiences future unexplained crashes:

- Marginal DRAM
- DIMM compatibility issues
- Memory-controller instability
- XMP-related instability
- Motherboard DIMM-slot/contact problems
- IMC voltage/timing sensitivity
- PSU/power transient issues
- CPU instability
- PCIe device/driver instability not captured by AER
- Firmware/BIOS bugs

The current evidence does not establish any of these as the cause.

---

## 12. Current Assessment

### Confidence

**No confirmed hardware/RAS error.**

The investigation did not find evidence of a failing DIMM or CPU through the
available Linux error-reporting infrastructure.

The mixed memory configuration is nevertheless worth keeping in mind.

The system is running all memory at:

    3000 MT/s

rather than attempting to run the 3200 MT/s DIMMs at their nominal advertised
speed.

That is a more conservative operating point, but mixed kits can still have
different subtimings, ranks, ICs, SPD characteristics, and voltage behavior.

---

## 13. Recommended Future Testing

These tests are intentionally deferred unless the problem recurs.

### 13.1 Confirm actual OS-visible RAM

    free -h

    grep MemTotal /proc/meminfo

This should be approximately 48 GB installed, minus the normal kernel/device
reservations.

---

### 13.2 Capture BIOS memory configuration

On the next maintenance window, record:

- XMP enabled/disabled
- DRAM frequency
- DRAM voltage
- Command rate
- Primary timings
- Gear mode, if exposed
- CPU System Agent voltage
- CPU VCCIO voltage
- BIOS version

Do not change these settings solely based on the current investigation.

---

### 13.3 Run a long memory test

If instability returns, perform a boot-time memory test rather than relying
only on Linux RAS reporting.

Preferred procedure:

1. Boot a current MemTest86 or Memtest86+ environment. The Proxmox installer/boot media includes
   a Memtest86+ option directly on its boot menu, which avoids needing to build a separate USB
   stick if a Proxmox ISO is already on hand.
2. Run multiple complete passes.
3. Ideally test overnight.
4. Record the exact DIMM configuration and BIOS settings.
5. Any reproducible memory error should be treated as significant.

A single intermittent error is enough to justify further DIMM isolation testing.

---

### 13.4 Test DIMMs individually / by matched kit

If memory errors appear:

1. Power down completely.
2. Test one matched kit at a time.
3. Test the 32 GB kit separately.
4. Test the 16 GB kit separately.
5. If necessary, test individual DIMMs.
6. Test suspect DIMMs in known-good motherboard slots.
7. Record which physical DIMM/slot combinations fail.

The goal is to distinguish:

    DIMM failure
        vs
    DIMM-slot / motherboard issue
        vs
    mixed-kit compatibility issue

---

### 13.5 Temporarily run JEDEC defaults

If crashes continue and memory remains suspect:

- Disable XMP.
- Load BIOS optimized/default memory settings.
- Allow the motherboard to select JEDEC timings/frequency.
- Re-test stability.

If instability disappears at JEDEC settings but returns with XMP, that strongly
suggests a marginal memory/IMC/timing configuration rather than a simple dead DIMM.

---

### 13.6 Check WHEA after every suspected crash

    journalctl -k -b -1 --no-pager | \
      grep -iE 'WHEA|MCE|machine.check|hardware error|EDAC|memory|dram'

Also check the current boot:

    journalctl -k -b 0 --no-pager | \
      grep -iE 'WHEA|MCE|machine.check|hardware error|EDAC|memory|dram'

Look specifically for:

    Hardware Error
    Machine check
    MCE
    WHEA
    EDAC
    corrected
    uncorrected
    memory failure
    page allocation failure

---

### 13.7 Check MCE records after recurrence

    ras-mc-ctl --errors

    ras-mc-ctl --summary

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM mce_record ORDER BY rowid DESC LIMIT 20;'

Also inspect:

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM mc_event ORDER BY rowid DESC LIMIT 20;'

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM memory_failure_event ORDER BY rowid DESC LIMIT 20;'

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM aer_event ORDER BY rowid DESC LIMIT 20;'

---

### 13.8 Check trace buffers after a recurrence

Current tracing:

    cat /sys/kernel/debug/tracing/tracing_on

Dump the trace buffer:

    cat /sys/kernel/debug/tracing/trace | tail -500

Search specifically:

    grep -iE 'mce|memory|aer|ras|hardware' \
      /sys/kernel/debug/tracing/trace

---

### 13.9 Capture complete previous-boot evidence

After an unexpected reboot:

    journalctl --list-boots

    journalctl -b -1 -k --no-pager > /root/previous-boot-kernel.log

    journalctl -b -1 --no-pager > /root/previous-boot.log

Then search:

    grep -iE \
      'WHEA|MCE|machine.check|hardware error|EDAC|memory|dram|aer|pcie|watchdog|lockup|panic|oops' \
      /root/previous-boot-kernel.log

---

### 13.10 Check BIOS/UEFI version

Record:

    dmidecode -t bios

A BIOS update should only be considered after checking the motherboard
manufacturer's release notes and preserving the current configuration.

---

### 13.11 Check motherboard identity

If further memory-specific investigation is required:

    dmidecode -t system | \
      grep -E 'Manufacturer:|Product Name:|Version:'

    dmidecode -t baseboard | \
      grep -E 'Manufacturer:|Product Name:|Version:'

The exact Z490 motherboard model is important for determining supported DIMM
topology, BIOS behavior, memory QVL information, and available diagnostic
settings.

---

## 14. Recommended Diagnostic Policy

> ⚠️ **Do not make multiple hardware/BIOS changes at once.**

If the host is currently stable:

> Leave the working configuration alone and collect evidence if the problem
> recurs.

If the host becomes unstable again:

1. Preserve the previous boot's journal.
2. Check `ras-mc-ctl`.
3. Check WHEA/MCE/EDAC messages.
4. Check trace buffers.
5. Record BIOS/XMP state.
6. Only then begin controlled memory isolation.

Changing several variables simultaneously makes it difficult to determine which
change actually affected the failure.

---

## 15. Known Limitations

- This is a consumer Comet Lake-S / Z490 platform.
- No populated EDAC memory-controller device is exposed through
  `/sys/devices/system/edac/mc`.
- rasdaemon therefore cannot provide the same DIMM-level visibility available
  on many server platforms.
- A clean MCE/RAS database does not guarantee error-free DRAM.
- A marginal memory configuration can potentially crash before Linux gets an
  opportunity to record a useful RAS event.
- The installed memory consists of mixed Corsair part numbers/kits, and a mixed
  rated speed (3000 vs 3200 MT/s).
- All memory is currently configured at 3000 MT/s.
- No controlled DIMM-isolation or extended offline memory test was performed
  during this investigation.
- No memory failure was reproduced.
- The specific symptom that triggered this investigation was not recorded in this document
  (see Section 1 TODO).

---

## 16. Quick Reference

| Check | Command |
|---|---|
| Installed/usable RAM | `free -h` |
| Kernel RAM total | `grep MemTotal /proc/meminfo` |
| DIMM inventory | `dmidecode -t memory` |
| RAS errors | `ras-mc-ctl --errors` |
| RAS summary | `ras-mc-ctl --summary` |
| RAS database | `sqlite3 /var/lib/rasdaemon/ras-mc_event.db` |
| MCE capability | `rdmsr -a 0x179` |
| MCE global control | `rdmsr -a 0x17a` |
| MCE sysfs | `find /sys/devices/system/machinecheck -type f` |
| EDAC state | `find /sys/devices/system/edac/mc -type f` |
| Current kernel errors | `journalctl -k -b 0` |
| Previous kernel errors | `journalctl -k -b -1` |
| dmesg fallback (ring buffer) | `dmesg \| grep -iE 'mce\|edac\|whea'` |
| WHEA/MCE search | `journalctl -k -b -1 \| grep -iE 'WHEA|MCE|machine.check'` |
| PCI inventory | `lspci -nn` |
| PCIe AER search | `journalctl -k -b -1 \| grep -iE 'AER|PCIe|pcie'` |
| Trace enabled | `cat /sys/kernel/debug/tracing/tracing_on` |
| MCE trace enabled | `cat /sys/kernel/debug/tracing/events/mce/mce_record/enable` |
| RAS memory trace enabled | `cat /sys/kernel/debug/tracing/events/ras/mc_event/enable` |
| RAS AER trace enabled | `cat /sys/kernel/debug/tracing/events/ras/aer_event/enable` |
| Trace buffer | `cat /sys/kernel/debug/tracing/trace` |
| Boot history | `journalctl --list-boots` |

---

## 17. Final Disposition

**Status: CLOSED — no confirmed memory/RAS hardware fault.**

The investigation successfully established that:

- 48 GB RAM is installed.
- Memory is operating at 3000 MT/s.
- The DIMM configuration uses mixed Corsair kits/part numbers, including a mixed rated speed
  (3000 vs 3200 MT/s).
- Linux MCE infrastructure is active.
- MCE capability exposes 12 banks.
- Corrected errors are not configured to be ignored.
- rasdaemon is operational and its database is populated with the expected
  event schemas.
- No memory errors are currently recorded.
- No MCE errors are currently recorded.
- No Linux memory-failure events are recorded.
- No PCIe AER errors are recorded.
- No useful EDAC DIMM-level memory-controller telemetry is available on this
  platform (Comet Lake-S has no populated in-kernel EDAC memory-controller driver).

There is therefore insufficient evidence to identify RAM as the cause of the
original instability.

If the host remains stable, no immediate hardware change is recommended.

If the problem recurs, the next escalation should be an extended offline
memory test followed by controlled DIMM/kit isolation and, if necessary,
testing with XMP disabled / JEDEC defaults.
