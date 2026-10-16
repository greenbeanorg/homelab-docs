# SMART Doctor

Interactive SMART monitoring and extended self-test manager for the 4-drive
`tank` pool on `swearengen` (4x WDC WD100EMAZ-00WJTA0 10TB, ~7yr old shucks).

## Purpose

- Verify all four pool members are present and correctly identified by
  **serial number**, not `/dev/sdX` name (Linux can renumber `sdb`–`sde`
  across reboots; the script re-derives device paths by matching serial
  every run).
- Run one-drive-at-a-time extended (LONG) self-tests, since running more
  than one concurrently means they fight over the same controller/heads.
- Track SMART health over time via timestamped snapshots.
- Give a conservative, low-noise GOOD/WATCH/BAD verdict per drive.

## Location

```
$HOME/smart-doctor/smart-doctor.sh
```

Data directory (created on first run):

```
$HOME/smart-doctor/
├── history/   # SMART snapshots, one file per drive per snapshot
├── tests/     # completed long-test result logs
└── state/     # in-progress test tracking (start time, expected finish)
```

## Requirements

- `smartctl` (smartmontools)
- Run as root, or as a user with passwordless `sudo smartctl`

## Usage

```bash
./smart-doctor.sh
```

Menu-driven. Main options:

| Choice | Action |
|---|---|
| 1 | Start extended (LONG) SMART test on a selected drive |
| 2 | Monitor an in-progress test |
| 3 | Drive health details |
| 4 | Full SMART report (`smartctl -x`) |
| 5 | SMART history for a drive |
| 6 | Save a manual SMART snapshot |
| 7 | Refresh dashboard |

The dashboard (shown on launch and after every action) lists inventory
status, health verdict, temperature, last snapshot, and last long-test
result for all four drives at a glance — this is the table pasted into
chat when reviewing results.

## Diagnosis logic

`diagnose()` is intentionally conservative. It flags:

- **BAD** — overall SMART health = FAILED, or `Current_Pending_Sector`
  (197) > 0, or `Offline_Uncorrectable` (198) > 0
- **WATCH** — `Reallocated_Sector_Ct` (5) > 0, or `UDMA_CRC_Error` (199) > 0
- **GOOD** — none of the above

**Deliberately NOT used in the verdict:** power-on hours, Read Recovery
Attempts, Reported Uncorrectable Errors (device statistics log), or
PhyRdy/PhyNRdy counts. These are lifetime cumulative counters that
naturally return low nonzero numbers over years of normal operation and
would generate constant false alarms if treated as failures. They're
still surfaced in the per-drive detail view (option 3) as context, just
not as pass/fail criteria — a rising trend there is worth eyeballing, but
a small flat number is not a fire alarm by itself.

## Baseline reference (2026-08-28, full extended test pass)

All four drives: GOOD, 36–39°C, long test PASS, 4/4 present. Lifetime
Reported Uncorrectable Errors / Read Recovery Attempts: 4 / 4 on all
four drives — consistent across the fleet because they're identical
mechanisms, same firmware, born and run together for 7+ years; not
flagged by `diagnose()` and not a concern on their own. Reallocated /
pending / offline-uncorrectable counts were all 0 across the fleet at
this baseline.

## Baseline reference (2026-10-16, raw `smartctl -x` pull)

Second data point, about 103 days of 24/7 runtime after the first raw
SMART review (2026-07-05, 63,531 / 63,531 / 63,532 / 63,549 POH).

| Attribute | sdb `JEG8DNZN` | sdc `2YJH8MHD` | sdd `JEG8M03N` | sde `2YJ0VL0D` |
|---|---|---|---|---|
| Power-on hours (9) | 66,009 | 66,009 | 66,009 | 66,026 |
| Reallocated (5) / Pending (197) / Offline unc. (198) | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| UDMA CRC errors (199) | 0 | 0 | 0 | 0 |
| Helium level (22) | 100 | 100 | 100 | 100 |
| Load cycle count (193) | 8,944 | 9,000 | 8,849 | 9,029 |
| Temperature now / lifetime max | 38 / 48°C | 39 / 49°C | 40 / 49°C | 37 / 45°C |
| Read Recovery Attempts (devstat) | 633 | 2,694 | 808 | 24 |
| Reported Uncorrectable Errors (devstat) | 0 | 0 | 0 | 0 |
| Last extended test (lifetime hr) | PASS @ 64,756 | PASS @ 64,779 | PASS @ 64,801 | PASS @ 64,839 |

Verdict: all four GOOD, no movement in any failure-predictor attribute
since July. Error logs and Pending Defects logs are empty on all four.
Temperatures run 1–2°C warmer than the August baseline; sdd is the
warmest slot (40–41°C), well inside the 65°C spec.

**Correction to the 2026-08-28 baseline:** the raw device-statistics
log does not match the "4 / 4" Reported Uncorrectable / Read Recovery
values the dashboard showed. Raw values today are 0 Reported
Uncorrectable on every drive, and Read Recovery Attempts differ widely
per drive (above). The script is likely reading a different field for
those two counters. Compare future pulls against this table, not the
August dashboard numbers. Read Recovery Attempts on sdc is the counter
to watch: a fast climb there would make sdc the most likely first
failure.

## Notes

- Only run one LONG test at a time — the script warns but does not
  enforce this.
- LONG tests on these 10TB drives take roughly a full day each end to
  end; the script computes an estimated finish time from
  `smartctl -c` and tracks it in `state/`.
- A completed test is auto-detected on the next dashboard refresh
  (`check_test_completion`), which archives the result to `tests/` and
  takes a fresh snapshot automatically.

---

