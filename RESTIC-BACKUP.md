# Restic Off-Site Backup — TrueNAS → kk1 (Oracle Cloud), via WireGuard

Off-site backup of TrueNAS bulk storage using `restic`, targeting an SFTP repo
on the WireGuard hub (`kk1`, Oracle Cloud Ampere A1) over the overlay network
rather than the public internet.

- **Source host:** `truenas` (TrueNAS SCALE VM, VMID 1000)
- **Repo host:** `kk1` (Oracle Cloud, WireGuard hub, overlay `10.99.0.1`)
- **Status:** live — backup and check both scheduled and running
- **Date:** September 2026

---

## 1. Why this replaced the previous setup

[#1-why-this-replaced-the-previous-setup](#1-why-this-replaced-the-previous-setup)

The original restic pipeline ran from `swearengen`, exporting Proxmox/OPNsense/
Home Assistant configs into a local staging directory and backing up *that*
directory to a remote VPS. It broke after the mdadm→ZFS migration (see
`TRUENAS.md`) and was never re-pointed.

Rather than repair the old pipeline in place, backups were rebuilt from
scratch, scoped narrowly, and moved to run directly from TrueNAS:

- **TrueNAS is the actual data source** — running the job there removes a
  hop (swearengen no longer needs to be in the path at all) and one less
  thing that can silently break the chain.
- **The old `/data` repo on kk1 predated the ZFS migration** and was not
  trusted — renamed aside (`/data.old-YYYY-MM-DD`) rather than deleted
  outright, and a fresh repo was `restic init`'d in its place.
- **The public-IP repo target was replaced with the WireGuard overlay
  address** (`10.99.0.1`) — traffic now stays inside the existing
  hub-and-spoke tunnel, so the SFTP/SSH endpoint on kk1 never needs to be
  reachable from the open internet, and restic's own encryption sits on
  top of the tunnel encryption.
- **Scope was narrowed on purpose.** The old script backed up five
  different hosts' configs; this one backs up a single TrueNAS dataset
  (`/mnt/tank/storage/important`). Proxmox/OPNsense/Home Assistant config
  export is a separate, not-yet-rebuilt piece of work (see Honest caveats).

---

## 2. Repo location & connection

[#2-repo-location--connection](#2-repo-location--connection)

```
RESTIC_REPOSITORY="sftp:aba@10.99.0.1:/data"
```

`10.99.0.1` is kk1's WireGuard overlay address (the hub, per the existing
hub-and-spoke VPN topology). SSH key auth is already in place from TrueNAS to
kk1; no password prompt.

**Capacity — checked, currently fine.** kk1's root volume (`/dev/sda1`, 194G)
sat at 51% used / 95G free after the first full backup landed (108.166 GiB
added, 92.757 GiB stored post-compression). No dedicated block volume was
needed. Revisit if `important` grows substantially — `restic stats` gives
current repo size; `df -h` on kk1 gives headroom.

---

## 3. Scripts

[#3-scripts](#3-scripts)

All under `/mnt/tank/storage/aba/scripts/` on TrueNAS — deliberately kept on
the pool, not in `/etc` or `/root`, since TrueNAS SCALE manages its own OS via
boot environments and anything dropped outside the pool is not guaranteed to
survive an upgrade. Same reasoning applies to scheduling (§5).

### 3.1 `restic.env`

[#31-resticenv](#31-resticenv)

```bash
export RESTIC_REPOSITORY="sftp:aba@10.99.0.1:/data"
export RESTIC_PASSWORD_FILE="/mnt/tank/storage/aba/.config/restic/password"
export RESTIC_CACHE_DIR="/mnt/tank/storage/aba/.cache/restic"
```

`RESTIC_CACHE_DIR` is deliberately on the pool, not `/var/cache` — the boot
pool is small and reserved for the OS; cache contents are regenerable, but
there's no reason to spend boot-pool space on them.

Password file permissions: `chmod 600`.

### 3.2 `restic_backup.sh`

[#32-restic_backupsh](#32-restic_backupsh)

```bash
#!/bin/bash
set -uo pipefail

source /mnt/tank/storage/aba/scripts/restic.env
[[ ! -r "$RESTIC_PASSWORD_FILE" ]] && { echo "ERROR: RESTIC_PASSWORD_FILE missing or unreadable"; exit 1; }
mkdir -p "$RESTIC_CACHE_DIR"

SOURCE=/mnt/tank/storage/important

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

restic unlock --remove-all 2>/dev/null || true

if restic backup --verbose --compression max "$SOURCE"; then
    log "backup OK"
else
    log "FAILED: restic backup"
    exit 1
fi

restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
log "done"
```

Retention (`--keep-daily 7 --keep-weekly 4 --keep-monthly 6`) is deliberately
lighter than the old swearengen script's config-backup policy
(`--keep-daily 30 ... --keep-within 90d`) — that policy suited fast-changing,
small configs; this is a slower-moving bulk dataset, where weekly/monthly
coverage matters more than 30 days of near-identical dailies.

### 3.3 `restic_check.sh`

[#33-restic_checksh](#33-restic_checksh)

```bash
#!/bin/bash
set -uo pipefail

source /mnt/tank/storage/aba/scripts/restic.env
[[ ! -r "$RESTIC_PASSWORD_FILE" ]] && { echo "ERROR: RESTIC_PASSWORD_FILE missing or unreadable"; exit 1; }

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

log "starting restic check"

if restic check --read-data; then
    log "restic check OK"
    exit 0
else
    log "restic check FAILED"
    exit 1
fi
```

`--read-data` actually re-reads and verifies backed-up data from the repo, not
just repo metadata — the only way to be sure a backup is real and not just
"restic said done."

---

## 4. Testing sequence used

[#4-testing-sequence-used](#4-testing-sequence-used)

1. Renamed old `/data` on kk1 aside, created a fresh empty one.
2. `source restic.env && restic init` from TrueNAS — confirmed against the
   new WireGuard-only target.
3. `restic snapshots` — confirmed empty/healthy fresh repo.
4. `restic backup --dry-run --verbose --compression max` against the full
   source dataset — validated paths/permissions without writing or touching
   kk1's disk.
5. First real backup run by hand (not the wrapper script) inside a long-lived
   session to survive an SSH drop across a run long enough to outlast a home
   WAN uplink: 257,649 files / 122.115 GiB scanned, all new, 6h11m,
   108.166 GiB added / 92.757 GiB stored (~14% compression).
6. `restic_check.sh` run against the fresh repo — `restic check --read-data`,
   5,630 packs, **no errors found** (52m34s — re-reads all data from kk1 over
   the WAN link, so this is the slow, thorough check, not a quick one).
7. `restic_backup.sh` (the actual wrapper, not the raw command) run by hand
   to confirm the script itself works end-to-end — env sourcing, password
   file check, unlock, backup, forget/prune, under the account cron will
   run as. Second run found the parent snapshot and completed incrementally
   in 16 seconds (8 files changed, rest unmodified) — confirms steady-state
   nightly runtime will be seconds, not hours.
8. Both scripts wired into TrueNAS's Cron Job UI (System Settings → Advanced
   → Cron Jobs) — not a hand-edited `crontab -e`, for the same
   boot-environment survival reasoning as §3.

### Dry-run performance note

[#dry-run-performance-note](#dry-run-performance-note)

The initial dry-run estimated ~51 minutes for the full dataset.
`zpool iostat tank 2` during the run showed 1–2.7K read IOPS at only
80–170MB/s bandwidth — small, scattered reads rather than large sequential
ones, meaning the run was **disk-seek-bound on the RAIDZ1 array**, not
CPU/hashing-bound. Concluded not worth tuning (`--read-concurrency`,
`GOMAXPROCS`) for a one-time full scan — confirmed correct in practice: the
second (incremental) run took 16 seconds, since restic's change-detection
means every subsequent backup only re-reads files that actually changed.

---

## 5. Scheduling

[#5-scheduling](#5-scheduling)

Scheduled through **TrueNAS's own Cron Job UI**, not a hand-edited
`crontab -e` — the same boot-environment survival reasoning as §3.
`restic_backup.sh` runs on the primary (daily) schedule; `restic_check.sh`
runs separately and less frequently (weekly) and offset from the backup time
so the two don't overlap and fight over the repo lock. Both jobs are live as
of this writing.

---

## Honest caveats

[#honest-caveats](#honest-caveats)

- **Proxmox/OPNsense/Home Assistant config export** (the original purpose of
  the swearengen script) is **not yet rebuilt** — this runbook only covers
  the TrueNAS bulk-data path. Judged low-effort to redo when picked back up,
  so not currently a priority.
- **A pre-existing rootfs-style mirror of `swearengen`** (`/etc`, `/root`,
  live-patch state, SSL cert bundles) was found living inside
  `important/backup/swearengen/` — origin and freshness unconfirmed as of
  this writing; may be a stale leftover from an earlier, unrelated backup
  approach. Not investigated yet; flagged here so it isn't lost track of.
- **Cron output/alerting is not wired into the existing monitoring stack.**
  TrueNAS cron jobs email output by default; whether that lands anywhere
  useful depends on whether system email is configured. No ntfy/Uptime Kuma
  hook exists yet for backup success/failure — the script's `log()`/`exit 1`
  pattern is ready for that, but the wiring itself is a later task.
- Retention values (`7/4/6`) are a starting guess, not derived from any
  actual RPO requirement — revisit once real usage patterns on `important`
  are observed over a few months.

---

## Quick reference

[#quick-reference](#quick-reference)

| Item              | Value                                                |
| ----------------- | ----------------------------------------------------- |
| Source            | `truenas` — `/mnt/tank/storage/important`             |
| Repo              | `sftp:aba@10.99.0.1:/data` (kk1, via WireGuard)       |
| Scripts           | `/mnt/tank/storage/aba/scripts/` (pool, not `/etc`)   |
| Env file          | `restic.env`                                          |
| Password file     | `~/.config/restic/password` (chmod 600)               |
| Cache dir         | `~/.cache/restic` (pool, not boot pool)               |
| Retention         | 7 daily / 4 weekly / 6 monthly                        |
| Scheduling        | TrueNAS Cron Job UI — live                            |
| First backup      | 122.115 GiB scanned, 92.757 GiB stored, 6h11m         |
| Incremental       | ~16s for a typical daily delta                        |
| Integrity check   | `restic check --read-data` passed, 0 errors           |
| Open items        | swearengen rootfs origin unconfirmed; PVE/OPNsense/HA export not rebuilt; no alerting hook yet |
