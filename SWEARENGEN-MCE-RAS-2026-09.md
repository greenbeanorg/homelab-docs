# swearengen Memory / MCE / RAS Investigation — 2026-09

Status: **REOPENED 2026-10-08** — 18 corrected MCEs recorded between 2026-09-15 and 2026-10-07.
All are bank 0 (instruction fetch unit) internal parity errors, all corrected, none uncorrected.
RAM has been ruled out as the source of these events. Investigation is active and under
observation; see Sections 11–14 and 16.

Investigation originally performed: 2026-09-14 (closed 2026-09-14, reopened 2026-10-08)

Diagnostic runbook for `swearengen`, the primary Proxmox host, covering investigation of possible
memory instability, Machine Check Exceptions (MCE), EDAC/RAS reporting, and PCIe AER errors.

The host has 48 GB of installed system memory.

> ⚠️ **Revision note (2026-10-08).** The original 2026-09-14 pass found a clean RAS database and
> closed the investigation. It was reopened when `rasdaemon` began recording MCE events. The
> original Sections 1–10 are preserved as the record of that first pass, with two factual
> corrections marked inline in Section 6. Sections 11–14 are new. Sections 15–21 have been
> rewritten to reflect current findings.

---

## Contents

1. [Symptom / Investigation Trigger](#1-symptom--investigation-trigger)
2. [Result — First Pass (2026-09-14)](#2-result--first-pass-2026-09-14)
3. [Hardware / Memory Configuration](#3-hardware--memory-configuration)
4. [RAS / rasdaemon Investigation](#4-ras--rasdaemon-investigation)
5. [Kernel Trace Configuration](#5-kernel-trace-configuration)
6. [MCE Capability / Machine Check State](#6-mce-capability--machine-check-state)
7. [EDAC Investigation](#7-edac-investigation)
8. [rasdaemon Tracepoint Messages](#8-rasdaemon-tracepoint-messages)
9. [PCIe Devices](#9-pcie-devices)
10. [Relevant Log Search](#10-relevant-log-search)
11. [MCE Event Record (2026-09 → 2026-10)](#11-mce-event-record-2026-09--2026-10)
12. [MCE Status Decode](#12-mce-status-decode)
13. [Rate, Distribution, and Configuration Timeline](#13-rate-distribution-and-configuration-timeline)
14. [hwerr-check.sh — New-Error Detection](#14-hwerr-checksh--new-error-detection)
15. [What Was Ruled Out](#15-what-was-ruled-out)
16. [Current Assessment](#16-current-assessment)
17. [Recommended Future Testing](#17-recommended-future-testing)
18. [Recommended Diagnostic Policy](#18-recommended-diagnostic-policy)
19. [Known Limitations](#19-known-limitations)
20. [Quick Reference](#20-quick-reference)
21. [Final Disposition](#21-final-disposition)

---

## 1. Symptom / Investigation Trigger

### Original trigger (2026-09-14)

> ⚠️ **TODO (still open):** the specific symptom that prompted the original investigation —
> crash, VM stall, unexpected reboot, log message noticed — was never recorded, and the
> journal for that period has since rotated. One candidate remains visible in the boot
> history: boot `-2` ended 2026-09-04 02:53 and the next boot did not start until 05:30, a
> ~2.5 hour unexplained gap in the middle of the night. That has not been confirmed as the
> trigger.

The first pass was performed to determine whether the host had evidence of:

- DRAM/memory instability
- CPU Machine Check Exceptions (MCE)
- EDAC-reported corrected or uncorrected memory errors
- Linux `memory_failure` events
- PCIe Advanced Error Reporting (AER) errors
- Other RAS-reported hardware errors
- Possible memory configuration / XMP-related instability

### Reopening trigger (2026-10-08)

`rasdaemon` recorded its first MCE on 2026-09-15, one day after the investigation closed. By
2026-10-08 the database held 18 events. All are corrected `Internal parity error` records in
machine-check bank 0. The reopening is about an observed, recurring, logged hardware event —
not a crash or a stall. The host has remained stable throughout.

---

## 2. Result — First Pass (2026-09-14)

### Conclusion as recorded on 2026-09-14

No hardware error was captured by the Linux RAS/MCE infrastructure during the first pass.

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

### ⚠️ Important qualification (and what it turned out to mean)

**A clean RAS/MCE database does not prove that the memory subsystem is perfect.**

This is a consumer Intel Comet Lake-S / Z490 platform and the installed memory is not
a homogeneous server DIMM configuration. EDAC memory-controller reporting was not
registered for this system, so DIMM-level corrected-error telemetry is limited.

The original caveat was written as a hedge about memory. In practice the clean result was
accurate for memory and simply premature for the CPU: the first MCE arrived the following day.
The lesson worth keeping is that **a RAS database is a record of what has been observed so far,
not a verdict** — a point-in-time clean read on a low-rate intermittent fault means very little.

---

## 3. Hardware / Memory Configuration

### Platform

PCI identification:

    00:00.0 Host bridge [0600]:
    Intel Corporation Comet Lake-S 6c Host Bridge/DRAM Controller
    [8086:9b53] (rev 05)

Chipset:

    Intel Z490

Motherboard:

    ASUSTeK COMPUTER INC. TUF GAMING Z490-PLUS
    BIOS version 2601, release date 2021-10-29

CPU:

    Intel Core i5-10600K @ 4.10GHz
    Comet Lake-S, Family 6 Model 165 (0xA5) Stepping 5 — CPUID 0x000A0655
    6 cores / 12 threads

Microcode:

    0x100 (2024-11-14) — current; `intel-microcode 3.20251111.1~deb13u1` ships
    no newer revision for signature 0x000A0655.

Logical CPUs:

    12

Power limits (`/sys/class/powercap/intel-rapl:0`):

    long_term  (PL1): 125 W   — Intel stock for i5-10600K
    short_term (PL2): 182 W   — Intel stock for i5-10600K

The board is enforcing Intel's stock power limits. No "MultiCore Enhancement"-style
limit removal is in effect. (The setting itself is not exposed in EZ Mode on this board;
it lives under **Ai Tweaker** in Advanced Mode. Reading RAPL from Linux answers the
question without entering the BIOS.)

### Installed memory

Total installed memory:

    48 GB

`dmidecode` reports four populated DIMM locations:

| Slot | Part Number | Rated Speed | Configured Speed | Rank |
|---|---|---:|---:|---:|
| ChannelA-DIMM1 | CMK32GX4M2B3000C15 | 3000 MT/s | 3000 MT/s | 2 |
| ChannelA-DIMM2 | CMK16GX4M2B3200C16 | 3200 MT/s | 3000 MT/s | 1 |
| ChannelB-DIMM1 | CMK32GX4M2B3000C15 | 3000 MT/s | 3000 MT/s | 2 |
| ChannelB-DIMM2 | CMK16GX4M2B3200C16 | 3200 MT/s | 3000 MT/s | 1 |

The configuration therefore contains mixed Corsair memory part numbers / kits, mixed rated
speeds (3000 vs 3200), and mixed rank characteristics. The 3200-rated pair runs below its
rated speed to match the 3000-rated pair.

### Memory speed history

| Period | Configured speed | Cause |
|---|---|---|
| through 2026-09-19 18:06 | 3000 MT/s | XMP enabled |
| 2026-09-19 18:12 → 2026-09-23 ~11:00 | 2133 MT/s | XMP disabled during an unrelated BIOS session |
| 2026-09-23 ~11:00 → present | 3000 MT/s | XMP re-enabled |

This history matters because MCEs were recorded in **both** the XMP-on and XMP-off periods.
See Section 13.

### ⚠️ Important interpretation note

The strings `CMK32GX4M2...` and `CMK16GX4M2...` describe Corsair kit/product
identifiers and should not be interpreted as four individual DIMMs of 32/16 GB
respectively.

The system's actual installed RAM is 48 GB.

### Kit-matching note

The host `garrett` holds a second `CMK16GX4M2B3200C16` pair — the same part number as
swearengen's 8 GB pair. A swap could in principle produce matched configurations, but every
variant drops swearengen from 48 GB to 32 GB, against ~30 GB of running VM allocation plus
ZFS ARC on the TrueNAS guest. **Not recommended.** The gain would be 3000 → 3200 MT/s (~7%
bandwidth) at the cost of a third of the host's RAM. A matching part number also does not
guarantee matching ICs — the Corsair "Ver" code on the module label (e.g. 4.31 Samsung,
5.39 Micron, 3.31 Hynix) is the actual identity to compare.

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

### Error state as of 2026-10-08

All non-MCE categories remain clean. `mce_record` holds 18 rows — see Section 11.

    ras-mc-ctl --errors

Result (abbreviated):

    No Memory errors.
    No PCIe AER errors.
    No ARM processor errors.
    No CXL ... errors.          (all CXL categories clean)
    No Extlog errors.
    No devlink errors.
    No disk errors.
    No Memory failure errors.

    MCE events:
    1 2026-09-15 14:48:36 -0400 error: Internal parity error, ...
    [... 18 total, see Section 11]

> ⚠️ Note the asymmetry in `ras-mc-ctl` output: "No Memory errors" refers to the `mc_event`
> table (EDAC memory-controller events), which is permanently empty on this platform because
> no EDAC driver binds (Section 7). It is **not** a statement that memory is healthy, and it
> coexists happily with a populated `mce_record` table. Read both.

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

This means the relevant tracepoints were enabled during the investigation. The subsequent
capture of 18 MCE records confirms the path works end to end.

---

## 6. MCE Capability / Machine Check State

The `msr` kernel module was loaded and the Machine Check MSRs were readable.

    modprobe msr

### IA32_MCG_CAP (MSR 0x179)

    rdmsr -a 0x179

Result on every logical CPU:

    c0c

> ⚠️ **Correction (2026-10-08).** The original decode of the upper bits in this section was
> wrong. It attributed `0xc00` to `MCG_SER_P` and `MCG_EXT_CNT`. Those are bit 24 and bits
> 23:16 respectively, and both are **zero** here (expected on a consumer part with no software
> error recovery). The corrected decode follows.

`0xc0c` = `0000 1100 0000 1100b`. Bit by bit:

| Bits | Field | Value | Meaning |
|---|---|---|---|
| 7:0 | `Count` | `0x0C` = 12 | 12 machine-check banks |
| 8 | `MCG_CTL_P` | **0** | `IA32_MCG_CTL` is **not implemented** |
| 9 | `MCG_EXT_P` | 0 | No extended MCG registers |
| 10 | `MCG_CMCI_P` | **1** | Corrected Machine Check Interrupt supported |
| 11 | `MCG_TES_P` | **1** | Threshold-based error status present |
| 23:16 | `MCG_EXT_CNT` | 0 | — |
| 24 | `MCG_SER_P` | 0 | No software error recovery |

`MCG_CMCI_P` is the operationally relevant bit: CMCI is the mechanism by which these corrected
errors are delivered and logged at all.

### IA32_MCG_CTL (MSR 0x17a)

    rdmsr -a 0x17a

Result on every logical CPU:

    0

> ⚠️ **Correction (2026-10-08).** The original text concluded from this that "no global
> machine-check control mask was observed disabling the MCE mechanism." That conclusion is not
> supported. Because `MCG_CTL_P` (bit 8 of `IA32_MCG_CAP`) is **0**, `IA32_MCG_CTL` is not
> implemented on this CPU and reading it yields nothing meaningful. The read is not evidence
> either way.
>
> The actual evidence that MCE reporting is live is empirical: 18 events were subsequently
> captured and written to the rasdaemon database.

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

### ⚠️ Consequence for memory diagnosis

Because no EDAC driver binds **and** the installed memory is non-ECC, a genuine DRAM bit
flip will in most cases produce no log entry anywhere. Memory cannot be cleared or
convicted from Linux telemetry on this host. An offline Memtest86+ run is the only
instrument available for that question (Section 17.3).

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

PCIe AER reporting remains clean as of 2026-10-08:

    No PCIe AER errors.

No evidence was found that PCIe errors are contributing to the MCE pattern.

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
- WHEA hardware errors
- kernel memory failures
- explicit overclock failures

The rasdaemon messages concerning tracepoint access are documented separately
above.

As a fallback for future recurrence, also check `dmesg` directly rather than relying solely on
`journalctl -k`, since the ring buffer and the journal can diverge and `dmesg` may still hold an
event that has rotated out of the persisted journal:

    dmesg | grep -iE 'mce|edac|whea|hardware error'

### Benign messages seen alongside MCEs — do not chase these

These appear in the same `dmesg` window as the MCE lines and are unrelated:

| Message | What it actually is |
|---|---|
| `perf: interrupt took too long (N > M), lowering kernel.perf_event_max_sample_rate` | The kernel self-tuning its perf sampling rate because the sampling NMI exceeded its time budget. Routine on a busy virtualization host. Not a hardware error. |
| `sctp: Hash tables configured (bind 1024/1024)` | The SCTP module loaded. Informational. |
| `apparmor="DENIED" ... profile="tcpdump" ... family="unix"` | The stock AppArmor `tcpdump` profile blocking a Unix socket. Does not affect packet capture. |
| `usb 1-12: USB disconnect` | A USB device was unplugged. |

---

## 11. MCE Event Record (2026-09 → 2026-10)

All 18 events recorded to date. Every row is identical in kind: bank 0, status
`0x9000004000010005`, corrected, `Internal parity error`.

| # | Timestamp | CPU | APIC ID | Bank | Status | Severity |
|---:|---|---:|---:|---:|---|---|
| 1 | 2026-09-15 14:48:36 | 1 | 2 | 0 | `0x9000004000010005` | Corrected |
| 2 | 2026-09-20 08:53:00 | 1 | 2 | 0 | `0x9000004000010005` | Corrected |
| 3 | 2026-09-24 01:10:10 | 5 | 10 | 0 | `0x9000004000010005` | Corrected |
| 4 | 2026-09-24 21:11:50 | 3 | 6 | 0 | `0x9000004000010005` | Corrected |
| 5 | 2026-09-26 09:16:20 | 4 | 8 | 0 | `0x9000004000010005` | Corrected |
| 6 | 2026-09-26 11:18:00 | 3 | 6 | 0 | `0x9000004000010005` | Corrected |
| 7 | 2026-09-26 21:04:32 | 2 | 4 | 0 | `0x9000004000010005` | Corrected |
| 8 | 2026-09-27 11:32:50 | 2 | 4 | 0 | `0x9000004000010005` | Corrected |
| 9 | 2026-09-29 03:07:21 | 5 | 10 | 0 | `0x9000004000010005` | Corrected |
| 10 | 2026-09-29 20:22:09 | 0 | 0 | 0 | `0x9000004000010005` | Corrected |
| 11 | 2026-09-30 02:39:19 | 0 | 0 | 0 | `0x9000004000010005` | Corrected |
| 12 | 2026-10-01 04:43:26 | 2 | 4 | 0 | `0x9000004000010005` | Corrected |
| 13 | 2026-10-01 22:55:13 | 3 | 6 | 0 | `0x9000004000010005` | Corrected |
| 14 | 2026-10-03 05:03:37 | 3 | 6 | 0 | `0x9000004000010005` | Corrected |
| 15 | 2026-10-04 15:37:05 | 2 | 4 | 0 | `0x9000004000010005` | Corrected |
| 16 | 2026-10-04 16:22:03 | 4 | 8 | 0 | `0x9000004000010005` | Corrected |
| 17 | 2026-10-05 22:53:30 | 3 | 6 | 0 | `0x9000004000010005` | Corrected |
| 18 | 2026-10-07 18:21:14 | 2 | 4 | 0 | `0x9000004000010005` | Corrected |

Query that produces this (note `status` is stored as a signed 64-bit integer; `printf`
converts it back to the hex form the kernel prints):

    sqlite3 -header -column /var/lib/rasdaemon/ras-mc_event.db \
      "SELECT timestamp, cpu, apicid, bank, printf('0x%016x', status) AS status,
              mcistatus_msg AS severity, error_msg
         FROM mce_record ORDER BY rowid;"

### Correlating an event with what was running

The rasdaemon journal line for each event carries the PID and CPU of the task that was
running when the error was logged, in the form `<...>-195015 [005]`. That PID is normally
gone by the time the event is noticed, but if caught promptly:

    ps -p <pid> -o pid,comm,args

If the PID repeatedly resolves to a QEMU vCPU thread, the KVM angle becomes worth pursuing.
It has not so far.

---

## 12. MCE Status Decode

Status value `0x9000004000010005`, per Intel SDM Vol. 3B, `IA32_MCi_STATUS`:

| Bits | Field | Value | Meaning |
|---|---|---|---|
| 63 | `VAL` | 1 | Record is valid |
| 62 | `OVER` | 0 | No overflow — no event was lost |
| 61 | `UC` | **0** | **Corrected.** Error did not corrupt execution |
| 60 | `EN` | 1 | Reporting was enabled for this error |
| 59 | `MISCV` | 0 | No `MCi_MISC` data |
| 58 | `ADDRV` | 0 | **No address logged** |
| 57 | `PCC` | 0 | Processor context not corrupted |
| 52:38 | `CEC` | 1 | Corrected error count for this record |
| 31:16 | `MSCOD` | `0x0001` | Model-specific sub-code (not publicly documented) |
| 15:0 | `MCACOD` | `0x0005` | Simple error code 5 — **internal parity error** |

### Why this is a CPU error, not a memory error

`MCACOD` `0x0005` is an *architecturally defined simple error code*. It is not a
memory-hierarchy code (`0000 0001 RRRR TTLL`) and not a memory-controller code (`1MMM CCCC`).
A DRAM or IMC problem would surface as one of those instead. Combined with `ADDRV = 0` — no
physical address was associated with the error — the record describes a parity failure in a
structure internal to the core.

### What bank 0 means

On Skylake-derived client cores, including Comet Lake, **machine-check bank 0 is the IFU
(Instruction Fetch Unit)**. A parity failure in the L1 instruction cache is recoverable by
construction: instruction cache lines are read-only copies of memory, so the core discards the
line and refetches from L2. Nothing is lost and no data is at risk. This is consistent with
`UC=0`, `PCC=0`, and `ADDRV=0`.

### Prior art

This exact status value on bank 0 is widely reported across Intel generations — Ivy Bridge
(i5-3570K on Asus P8Z77), Haswell Xeon, Skylake, and notably Comet Lake (i9-10850K, and an
i5/i7-class Family 6 Model 165 Stepping 5 part reporting on CPU 5 / APIC ID `a`, an exact
match for this host's signature and core). It is a known and recurring event class on this
silicon, not an exotic failure mode.

---

## 13. Rate, Distribution, and Configuration Timeline

### Rate over time

| Window | Memory config | Events | Rate |
|---|---|---:|---:|
| 2026-09-14 → 09-23 ~11:00 | 3000 (XMP), then 2133 from 09-19 | 2 | ~0.22/day |
| 2026-09-23 ~11:00 → 10-01 00:00 | 3000 (XMP) | 9 | ~1.2/day |
| 2026-10-01 → 10-08 06:52 | 3000 (XMP) | 7 | ~0.96/day |

> ⚠️ **This is a step, not a ramp.** An intermediate reading on 2026-09-27 (6 events in 3.5
> days) was interpreted at the time as a rising trend suggesting progressive degradation. Two
> further weeks of data did not support that: the rate stepped up once around 2026-09-23 and
> has since held flat at roughly 1/day for ~15 days. Progressive silicon degradation does not
> plateau for two weeks. A configuration that changed once and stayed changed does.
>
> The 2026-09-23 BIOS session (XMP re-enabled) is the only known change at that boundary.

### Core distribution

| CPU | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---:|---:|---:|---:|---:|---:|
| Events | 2 | 2 | 5 | 5 | 2 | 2 |

All six physical cores are represented. The first two events both landed on core 1, which
briefly suggested a single marginal core; that hypothesis is dead. Cores 2 and 3 carrying 10
of 18 is within what random distribution produces at this sample size.

> ⚠️ **An APIC-ID observation worth explicitly retracting.** Every event carries an even APIC
> ID (thread 0 of its core), never an odd one. This is an artifact of how bank 0 is reported,
> not a signal: bank 0 is a core-level resource shared by both SMT threads, so attribution to
> the lower-numbered logical CPU is the expected behavior. Do not read anything into it.

### Load correlation

None found. The 2026-09-24 01:10 event is the best-characterized: the only scheduled job
near it was `restic_backup.service` at 01:00, which failed immediately at `EXEC` because
`/usr/local/bin/restic_backup.sh` no longer existed. The host was effectively idle. Events
are spread across all hours including 02:39, 03:07, and 05:03.

This argues against a thermal or load-related trigger and, if anything, toward behavior at
low load or during C-state transitions. Core temperature under a pinned two-thread
`stress-ng` run on core 1 peaked at 65 °C (limit 80, critical 100) — thermally unremarkable.

> ⚠️ A short stress run cannot falsify this. At ~1 event/day, a 4-minute run has no
> meaningful chance of reproducing the error. A negative result from one is not evidence.

---

## 14. hwerr-check.sh — New-Error Detection

Because these events produce only a single opaque kernel line (`mce: [Hardware Error]:
Machine check events logged`) and no notification, a state-tracking checker was written to
report only what is new since the last run.

Location on `swearengen`:

    /root/hwerr-check.sh

State file:

    /var/lib/hwerr-check/state

### What it reads

- **rasdaemon database**, all nine event tables: `mce_record`, `mc_event`, `aer_event`,
  `memory_failure_event`, `extlog_event`, `non_standard_event`, `arm_event`,
  `devlink_event`, `disk_errors`. Tracks per-table `MAX(rowid)` so only new rows are shown.
- **Kernel journal across all boots**, matching machine check, hardware error, EDAC, AER,
  PCIe Bus Error, memory failure, I/O errors, and NVMe/ATA errors. Messages that appear on
  every normal boot (`EDAC MC: Ver:`, `AER: enabled with IRQ`) are filtered out.

### Usage

    ./hwerr-check.sh              # new since last run (first run sets a baseline)
    ./hwerr-check.sh --all        # full history; does not advance state
    ./hwerr-check.sh --quiet      # silent unless something is new — for cron
    ./hwerr-check.sh --no-update  # report without advancing state

### Exit codes

| Code | Meaning |
|---:|---|
| 0 | Nothing new |
| 1 | New hardware errors found |
| 2 | Setup problem (not root, `sqlite3` missing, DB unreadable) |

The 0/1 split makes it directly wrappable for an Uptime Kuma push monitor, following the
same pattern as `picheck.sh`.

### ⚠️ What it cannot see

On this host, a real DRAM bit flip at XMP speeds will almost never appear in this script's
output — the RAM is non-ECC and no EDAC driver binds (Section 7). CPU, PCIe, and disk errors
will appear. Memtest86+ remains the only instrument for the memory question.

---

## 15. What Was Ruled Out

### Confirmed present

- **Corrected CPU machine-check errors**: 18 records, bank 0 (IFU), internal parity,
  2026-09-15 through 2026-10-07.

### Confirmed absent

As of 2026-10-08, across 18 events and three weeks:

- Uncorrected (`UC=1`) errors — **none**
- Errors in any bank other than 0 — **none**
- Any status value other than `0x9000004000010005` — **none**
- Overflow (`OVER=1`, indicating a lost event) — **none**
- EDAC-reported corrected or uncorrected memory errors (none possible, see Section 7)
- Linux memory-failure events
- PCIe AER errors
- CXL memory errors
- Extlog hardware errors
- Disk errors reported through rasdaemon
- Thermal or power-limit throttling events

### Eliminated as causes of the MCE pattern

| Hypothesis | How it was eliminated |
|---|---|
| RAM / DIMMs / XMP as sole cause | Events occurred both with XMP on (3000) and off (2133). `MCACOD 0x0005` is not a memory-hierarchy or memory-controller code; `ADDRV=0`, no address logged. |
| A single failing core | Events on all six physical cores. |
| Power-limit removal / board auto-OC | RAPL reports Intel stock PL1 125 W / PL2 182 W. |
| Stale microcode | Running 0x100 (2024-11-14); `iucode_tool` confirms it is the newest revision shipped for signature 0x000A0655. |
| 32-bit guest / QEMU interaction | All four VMs are 64-bit: `ellsworth` (101) Linux, `hearst` (102) FreeBSD 15.1 amd64, `nuttal` (201) Linux, `truenas` (1000) Linux. (`hearst` shows `ostype: other` in Proxmox, which only means the guest type was never declared.) |
| Heavy load / thermal | Events occur at idle, across all hours; core temp peaks at 65 °C under pinned load. |
| Progressive degradation | Rate plateaued at ~1/day for 15 days rather than continuing to climb. |

### Not conclusively ruled out

- A one-time configuration change at the 2026-09-23 BIOS session (**leading hypothesis**)
- Board firmware behavior — BIOS 2601 dates from 2021-10-29 and has not been updated
- IMC / system agent / VCCIO voltage sensitivity introduced by the XMP profile
- Baseline silicon behavior for this part — i.e. this CPU simply logs IFU parity events at
  ~1/day and always has, with the pre-09-23 rate reflecting an unobserved period rather than
  a genuinely lower rate
- Marginal DRAM (undetectable on this platform from Linux; Memtest86+ not yet run)
- CPU degradation on a longer timescale than three weeks

---

## 16. Current Assessment

### Severity: low. Confidence: moderate.

Corrected IFU parity errors do not corrupt data. The instruction cache holds read-only copies
and the core refetches on a parity failure. Across 18 events there has been no uncorrected
error, no other bank, no new signature, and no overflow. **The ZFS pool, the VMs, and host
data integrity are not at risk from this error class.** The host has been stable throughout.

What is unresolved is *why the rate stepped up*, not whether the errors are dangerous.

### The leading hypothesis

The step from ~0.2/day to ~1/day coincides with the 2026-09-23 BIOS session in which XMP was
re-enabled. On Asus boards, enabling XMP also adjusts system-agent and I/O voltages and may
apply other defaults alongside the memory profile. That is a one-time change that stayed
changed, which matches the observed step-and-plateau shape far better than progressive
degradation does.

It is not proven. The pre-09-23 period also had XMP enabled for most of its duration, at a
much lower rate — but that period is only 2 events, which is too few to establish a baseline
with any confidence.

### Escalation thresholds

Re-escalate immediately on any of:

- **Any** event with `UC=1` (uncorrected) — stop running production VMs on this host
- **Any** event in a bank other than 0
- **Any** status value other than `0x9000004000010005`
- `OVER=1` on any record (events being lost)
- Sustained rate above ~3/day

Any of those would change the diagnosis from "known benign error class at an unexplained
rate" to "CPU degrading," and would make CPU replacement the next step. A used i5-10600K is
inexpensive.

---

## 17. Recommended Future Testing

### 17.1 Next planned test — XMP-disabled interval

**This is the current next step.** One variable, bounded duration, decisive either way.

1. Disable XMP in the BIOS. Change nothing else.
2. Confirm: `dmidecode -t memory | grep 'Configured Memory Speed'` → expect 2133 MT/s.
3. Run `./hwerr-check.sh` daily for 7 days and record the count.

Interpretation:

| Result | Conclusion |
|---|---|
| ~0–1 events in 7 days | The 2026-09-23 session is implicated. Investigate which setting; consider running at JEDEC or at manually-set 3000 with stock SA/IO voltages. |
| ~7 events in 7 days | Memory profile is irrelevant. The CPU logs IFU parity at ~1/day regardless. Close the investigation and keep monitoring against the Section 16 thresholds. |

> ⚠️ Earlier reasoning in this investigation argued against running this test on the grounds
> that a rising trend would contaminate the result. That argument no longer applies — the rate
> is flat (Section 13), so a 7-day window is a clean comparison.

---

### 17.2 BIOS update (second, if 17.1 is inconclusive)

BIOS 2601 dates from 2021-10-29. Newer releases exist for the TUF GAMING Z490-PLUS and board
updates frequently carry voltage and memory-training changes.

Deferred to second position deliberately: it is a larger change with a less specific expected
outcome than 17.1. Check Asus release notes first and record the current configuration before
flashing.

After updating, re-run the 7-day count. A rate unchanged across a BIOS update rules out board
firmware.

---

### 17.3 Extended offline memory test

Still worth doing once, but note it addresses a **separate** question from the MCEs — it
tests DRAM at XMP, and the MCEs are not DRAM errors.

Procedure:

1. Shut down all VMs cleanly, especially the TrueNAS guest.
2. Pause the `greenbeanorg-kuma-reachable` healthchecks.io check, or expect it to fire —
   Uptime Kuma runs on `ellsworth`, which will be down.
3. Boot Memtest86+ from the Proxmox installer media (it is on the boot menu).
4. Confirm it is running in SMP/all-core mode — the header shows active core count. Single-core
   mode takes several times longer.
5. Run overnight.

Expected duration at 48 GB DDR4-3000 on this CPU: roughly **1.5–3 hours per pass**, so 2
passes in 3–6 hours and 3–4 passes overnight. The first completed pass gives the real
per-pass figure to plan from.

**Any error at all is a failure**, including a single error after hours of clean passes. On an
error, proceed to 17.4.

---

### 17.4 Test DIMMs individually / by matched kit

If memory errors appear:

1. Power down completely.
2. Test one matched kit at a time.
3. Test the 2×16 GB `CMK32GX4M2B3000C15` kit separately.
4. Test the 2×8 GB `CMK16GX4M2B3200C16` kit separately.
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

### 17.5 C-state experiment (optional, only if the rate climbs)

Events cluster at idle rather than under load, which is consistent with behavior during
C-state transitions. To test:

    # append to kernel command line, reboot, observe for 1-2 weeks
    intel_idle.max_cstate=1

Cost is several watts of additional idle power draw. Only worth running if 17.1 and 17.2 are
both inconclusive and the rate is rising.

---

### 17.6 Long-duration pinned stress

A meaningful stress test must run for hours, not minutes, given a ~1/day event rate.

    systemd-run --unit=core-stress \
      taskset -c <cpu>,<sibling> stress-ng --cpu 2 --cpu-method all -t 12h --metrics-brief

    journalctl -u core-stress -f
    ./hwerr-check.sh          # afterward

Find the SMT sibling with:

    cat /sys/devices/system/cpu/cpu<N>/topology/thread_siblings_list

Monitor temperature with (note the label is `Package id 0`, so `grep 'Core (1|Package)'`
silently matches nothing):

    sensors | grep -E '^(Package|Core 1:)'

`--cpu-method all` cycles through many code paths, which exercises the instruction cache
considerably better than a single tight loop.

---

### 17.7 Check WHEA/MCE after any suspected crash

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

### 17.8 Check MCE records after recurrence

    ./hwerr-check.sh --all

    ras-mc-ctl --errors
    ras-mc-ctl --summary

    sqlite3 -line /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM mce_record ORDER BY rowid DESC LIMIT 20;'

Also inspect:

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM mc_event ORDER BY rowid DESC LIMIT 20;'

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM memory_failure_event ORDER BY rowid DESC LIMIT 20;'

    sqlite3 /var/lib/rasdaemon/ras-mc_event.db \
      'SELECT * FROM aer_event ORDER BY rowid DESC LIMIT 20;'

---

### 17.9 Check trace buffers after a recurrence

Current tracing:

    cat /sys/kernel/debug/tracing/tracing_on

Dump the trace buffer:

    cat /sys/kernel/debug/tracing/trace | tail -500

Search specifically:

    grep -iE 'mce|memory|aer|ras|hardware' \
      /sys/kernel/debug/tracing/trace

---

### 17.10 Capture complete previous-boot evidence

After an unexpected reboot:

    journalctl --list-boots

    journalctl -b -1 -k --no-pager > /root/previous-boot-kernel.log

    journalctl -b -1 --no-pager > /root/previous-boot.log

Then search:

    grep -iE \
      'WHEA|MCE|machine.check|hardware error|EDAC|memory|dram|aer|pcie|watchdog|lockup|panic|oops' \
      /root/previous-boot-kernel.log

> 💡 The TSC field in an MCE record is a useful cross-check here. It counts from zero at boot,
> so a later event with a *lower* TSC than an earlier one proves a reboot occurred between
> them. This is how the 2026-09-19 BIOS session was located before `journalctl --list-boots`
> confirmed it (boot `-1` ended 18:06, boot `0` began 18:12).

---

### 17.11 Confirm actual OS-visible RAM

    free -h

    grep MemTotal /proc/meminfo

This should be approximately 48 GB installed, minus the normal kernel/device
reservations.

---

### 17.12 Capture BIOS memory configuration

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

> ⚠️ With mixed kits, the board applies **one** DIMM's XMP profile to all of them. If it
> selects the 3200 C16 profile, the 3000-rated pair is being pushed beyond its rating. Verify
> with `dmidecode -t memory | grep 'Configured Memory Speed'` after any XMP change and set
> 3000 manually if needed.

---

## 18. Recommended Diagnostic Policy

> ⚠️ **Do not make multiple hardware/BIOS changes at once.**

This investigation has repeatedly demonstrated the value of the rule. Every hypothesis
eliminated in Section 15 was eliminated because exactly one variable differed between two
observation windows.

If the host is stable and the error rate is within thresholds:

> Leave the working configuration alone, run `hwerr-check.sh` on a schedule, and collect
> evidence.

Before concluding anything from a rate change:

> ⚠️ **Give it enough time.** A 3.5-day window produced a "rising trend" reading that two
> further weeks disproved. Intermittent events at ~1/day need a 7-day minimum observation
> window per configuration, and preferably two weeks, before the rate means anything.

If the host becomes unstable, or an escalation threshold from Section 16 is crossed:

1. Preserve the previous boot's journal.
2. Run `hwerr-check.sh --all`.
3. Check WHEA/MCE/EDAC messages.
4. Check trace buffers.
5. Record BIOS/XMP state.
6. Only then begin controlled isolation.

---

## 19. Known Limitations

- This is a consumer Comet Lake-S / Z490 platform with non-ECC memory.
- No populated EDAC memory-controller device is exposed through
  `/sys/devices/system/edac/mc`, so rasdaemon cannot provide DIMM-level visibility.
- **DRAM errors are effectively invisible to Linux on this host.** Non-ECC memory plus no
  EDAC driver means a bit flip produces no log entry. Memory can only be tested offline.
- `MSCOD` (status bits 31:16, value `0x0001`) is model-specific and not publicly documented
  by Intel for client parts, so the precise internal structure that failed cannot be named
  beyond "bank 0 / IFU."
- The pre-2026-09-23 baseline rests on only 2 events and is too small to be a reliable
  baseline.
- No controlled DIMM-isolation or extended offline memory test has been performed. Memtest86+
  remains outstanding.
- No memory failure has been reproduced.
- The host has not been returned to BIOS Optimized Defaults, so the full set of settings
  changed in the 2026-09-23 session is not known.
- The specific symptom that triggered the original 2026-09-14 investigation was never
  recorded and the relevant journal has rotated (see Section 1).

---

## 20. Quick Reference

| Check | Command |
|---|---|
| **New hardware errors since last run** | `./hwerr-check.sh` |
| **Full hardware error history** | `./hwerr-check.sh --all` |
| MCE table, decoded | `sqlite3 -header -column /var/lib/rasdaemon/ras-mc_event.db "SELECT timestamp,cpu,apicid,bank,printf('0x%016x',status),mcistatus_msg,error_msg FROM mce_record ORDER BY rowid;"` |
| Installed/usable RAM | `free -h` |
| Kernel RAM total | `grep MemTotal /proc/meminfo` |
| DIMM inventory | `dmidecode -t memory` |
| Configured memory speed | `dmidecode -t memory \| grep 'Configured Memory Speed'` |
| Board + BIOS version | `dmidecode -t baseboard -t bios \| grep -E 'Manufacturer\|Product\|Version\|Release'` |
| CPU model | `grep -m1 'model name' /proc/cpuinfo` |
| Running microcode | `grep -m1 microcode /proc/cpuinfo` |
| Newest available microcode | `iucode_tool -l /lib/firmware/intel-ucode/06-a5-05` |
| Power limits (PL1/PL2) | `cat /sys/class/powercap/intel-rapl:0/constraint_{0,1}_power_limit_uw` |
| Core temps | `sensors \| grep -E '^(Package\|Core)'` |
| SMT sibling of a core | `cat /sys/devices/system/cpu/cpu<N>/topology/thread_siblings_list` |
| RAS errors | `ras-mc-ctl --errors` |
| RAS summary | `ras-mc-ctl --summary` |
| RAS database | `sqlite3 /var/lib/rasdaemon/ras-mc_event.db` |
| MCE capability | `rdmsr -a 0x179` |
| MCE sysfs | `find /sys/devices/system/machinecheck -type f` |
| EDAC state | `find /sys/devices/system/edac/mc -type f` |
| Current kernel errors | `journalctl -k -b 0` |
| Previous kernel errors | `journalctl -k -b -1` |
| dmesg fallback (ring buffer) | `dmesg \| grep -iE 'mce\|edac\|whea'` |
| PCI inventory | `lspci -nn` |
| Trace enabled | `cat /sys/kernel/debug/tracing/tracing_on` |
| MCE trace enabled | `cat /sys/kernel/debug/tracing/events/mce/mce_record/enable` |
| Trace buffer | `cat /sys/kernel/debug/tracing/trace` |
| Boot history | `journalctl --list-boots` |

---

## 21. Final Disposition

**Status: OPEN — corrected CPU MCE pattern under observation, cause of rate step unresolved.**

### Established

- 18 corrected machine-check events between 2026-09-15 and 2026-10-07, all identical: bank 0
  (IFU), status `0x9000004000010005`, internal parity error.
- The error class is corrected and non-data-corrupting. No uncorrected event, no other bank,
  no other status value, no overflow.
- RAM is not the source of these events: `MCACOD 0x0005` is not a memory code, no address is
  logged, and events occurred both with XMP enabled and disabled.
- Events are distributed across all six physical cores — not a single failing core.
- The host is running Intel stock power limits, current microcode, and all-64-bit guests.
- Events occur at idle and show no load or thermal correlation.
- The rate stepped up once around 2026-09-23 and has since held flat at ~1/day for 15 days.

### Unresolved

- Why the rate stepped up. The 2026-09-23 BIOS session (XMP re-enabled) is the leading
  candidate but is not proven.
- Whether ~1/day is in fact this CPU's baseline and the earlier low rate was an artifact of a
  2-event sample.
- DRAM health at XMP — untestable from Linux on this platform, Memtest86+ outstanding.

### Next actions

1. **7-day XMP-disabled interval** (Section 17.1) — the decisive test, one variable.
2. BIOS update from 2601, if 17.1 is inconclusive (Section 17.2).
3. Memtest86+ overnight (Section 17.3) — separate question, still worth closing.
4. Continue running `hwerr-check.sh` and watch the Section 16 escalation thresholds.

### Operational posture

No hardware change is recommended at this time. The host continues to run production VMs.
That posture changes immediately on any uncorrected error, any error in another bank, or a
sustained rate above ~3/day.
