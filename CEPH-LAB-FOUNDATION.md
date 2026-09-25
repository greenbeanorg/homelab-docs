# Ceph Lab — Foundation (MON, MGR, Quorum, OSDs)

**Host:** garrett (Proxmox VE 9.2, i7-7700K, 16GB RAM)
**VMs:** ceph1 (401), ceph2 (402), ceph3 (403) — Ubuntu 24.04, 2 vCPU / 3GB each
**Purpose:** Expanding the homelab's infrastructure skill set by adding
distributed/clustered storage (Ceph) alongside the existing virtualization,
networking, and container work. Not production. Isolated from existing
garrett workloads (Icinga2, PostgreSQL, K3s) via dedicated lab-only bridges.

## The plain-language version, first

Before the jargon, here's the mental model that made this click for me:

- **A MON is like a shared whiteboard.** It doesn't hold any of your actual
  files — it holds the *facts everyone agrees on*: which disks exist, which
  ones are alive, who's allowed to store what. Every other piece of Ceph
  checks the whiteboard before doing anything.
- **You keep 3 whiteboards (not 1) so losing one doesn't stop the business.**
  They vote on every fact. As long as *more than half* are up and agree, work
  continues. Lose 1 of 3 → 2 remain → that's still a majority → nothing stops.
  Lose 2 of 3 → 1 remains → that's NOT a majority → everything freezes rather
  than risk two whiteboards disagreeing. This is why Ceph (and most cluster
  software) always wants an odd number of these.
- **An OSD is a warehouse worker who owns exactly one shelf (disk).** Their
  only job is storing and fetching boxes on that one shelf. More workers =
  more shelves = more total storage, and workers on different shelves can
  cover for each other if one goes home sick.
- **CRUSH is the rulebook the workers use to decide who gets a copy of what.**
  The rule that matters most: "don't put two copies of the same box on
  shelves owned by workers in the same building." That's what "spread
  replicas across hosts, not just across disks" actually means in practice.
- **A MGR is an assistant who handles the paperwork/dashboards** so the
  whiteboard-keepers (MONs) aren't distracted from their one job (agreeing on
  facts).

## What each piece is actually called, and does

| Plain-language name        | Real name | What it does |
|---|---|---|
| The shared whiteboard      | **MON** (Monitor) | Stores/votes on the cluster's map of truth — not your data. Needs a majority ("quorum") of MONs to agree before anything changes. |
| The assistant              | **MGR** (Manager) | Runs dashboards, metrics, automation modules. Takes load off the MONs. One active, others standby. |
| The warehouse worker       | **OSD** (Object Storage Daemon) | Owns one physical/virtual disk. Actually reads and writes your data. |
| The placement rulebook     | **CRUSH** (Controlled Replication Under Scalable Hashing) | The algorithm that decides which OSD(s) hold which piece of data, based on a hierarchy of hosts/racks/disks. |
| A shard of data            | **PG** (Placement Group) | A bucket that groups objects together so Ceph can replicate/track them as a unit instead of one object at a time. |

## Two states, not one, per worker (OSD)

Easy to conflate these — they're independent:

- **up / down** — "is this worker currently clocked in and reachable *right
  now*." A reboot makes a worker temporarily `down`.
- **in / out** — "is this worker still on the roster to be handed new work."
  You can deliberately mark a worker `out` (draining them before they leave
  permanently) while they're still clocked `in` and running.

A worker can be down-but-in (temporarily gone, still expected back) or
up-but-out (still working, just not being given anything new). Four
combinations, not a simple on/off.

## What we actually built today

### 1. Bootstrap the first whiteboard (MON) + assistant (MGR) on ceph1

```bash
cephadm bootstrap --mon-ip <ceph1-public-ip> --cluster-network <cluster-subnet>/24
```

- `--mon-ip` — which network the whiteboard listens on (kept on a lab-only
  isolated network, not the management/DHCP network the VM also has).
- `--cluster-network` — which network the workers use to talk *to each
  other* (replication/recovery traffic), kept separate from client/whiteboard
  traffic.

**Gotcha hit:** bootstrap failed with `Fatal glibc error: CPU does not
support x86-64-v2`. Cause: Proxmox's default virtual CPU type doesn't expose
modern CPU instructions to the guest, even though the real host CPU
(i7-7700K) supports them fine. **Fix:** set `cpu: host` on the VM (passes the
real CPU's feature set straight through) and fully stop/start the VM — a
soft reboot doesn't apply a CPU type change, only a full stop/start does.

### 2. Add the other two whiteboards

```bash
ceph orch host add ceph2 <ceph2-public-ip>
ceph orch host add ceph3 <ceph3-public-ip>
```

**Gotcha hit:** failed with "No container engine binary found." Cause:
bootstrap auto-installs Docker on the *first* node only — adding a node
later doesn't install anything for you, it just checks prerequisites.
**Fix:** `apt-get install -y docker.io` on ceph2/ceph3 first.

### 3. Clock sync (the "boring" issue that looked like a Ceph problem)

Health showed `clock skew detected on mon.ceph2` after adding it. The
whiteboard-voting protocol (Paxos) needs everyone's clocks close together —
by default within 50ms — because it depends on ordering events correctly.

**Root cause:** the VLAN's DHCP-advertised time server wasn't actually
answering NTP requests (confirmed with a direct UDP/123 test to an external
time server, which worked fine — so it was that specific server, not a
firewall/network block).

**Fix:** pointed the boxes at known-good external time servers explicitly,
then switched from `systemd-timesyncd` to `chrony` (converges faster,
handles VM clock behavior better than timesyncd).

**Lesson for the interview:** a Ceph health warning doesn't always mean
Ceph is broken — clock skew, DNS, and network path issues one layer down
routinely surface *as* a Ceph warning. Traced it down instead of just
restarting things blindly.

### 4. Proved quorum actually works, not just looks right

```bash
qm stop 403                    # hard-stop ceph3's whole VM, not just the service
ceph -s                        # → quorum ceph1,ceph2 — 2 of 3, still majority
ceph osd pool create quorumtest 8   # proves the whiteboard still accepts real changes
qm start 403
ceph -s                        # → back to quorum ceph1,ceph2,ceph3, automatically
```

No manual steps needed to rejoin — once a host is known to the cluster,
temporary unavailability heals itself.

### 5. Turned raw disks into workers (OSDs)

Each VM has a second, empty 20GB disk (`/dev/sdb`) set aside for exactly
this. Verified it was empty first (`lsblk`, no filesystem, no partitions) —
same "verify before you wipe" habit as the physical SSDs on garrett.

```bash
ceph orch daemon add osd ceph1:/dev/sdb
ceph orch daemon add osd ceph2:/dev/sdb
ceph orch daemon add osd ceph3:/dev/sdb
```

Result: `HEALTH_OK`, 3 up/in workers, one per host.

```bash
ceph osd tree
```
```
-1         0.05846  root default
-3         0.01949      host ceph1
 0    hdd  0.01949          osd.0       up
-5         0.01949      host ceph2
 1    hdd  0.01949          osd.1       up
-7         0.01949      host ceph3
 2    hdd  0.01949          osd.2       up
```

This tree *is* the CRUSH hierarchy — three separate "buildings" (hosts),
one worker (OSD) each. Once a pool exists with the default 3 copies, CRUSH's
rule sends one copy to each building, so losing any single host still
leaves 2 good copies.

## Verification commands worth memorizing

```bash
ceph -s              # the one command — health, quorum, OSD count, at a glance
ceph osd tree         # the CRUSH hierarchy, visually
ceph osd df           # per-worker usage and balance
```

## Still ahead

- Real pool + real data, then deliberately fail an OSD and watch PG states
  change (`active+clean` → `degraded` → recovery)
- RBD (block storage backed by Ceph)
- CephFS, RGW (time permitting)
- Physical SSDs as OSDs (Phase 2)
