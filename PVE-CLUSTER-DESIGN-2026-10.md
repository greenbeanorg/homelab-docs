# Proxmox Cluster Design — swearengen / garrett / wu

**Status:** DRAFT / theorycraft — design brainstorm, nothing here is executed or committed
**Started:** 2026-10-15
**Scope:** Converting three standalone Proxmox VE 9.2 hosts into a cluster with ZFS replication and selective HA, plus a dedicated Proxmox Backup Server

---

## 1. Why

- One management plane (UI/API) across all home Proxmox hosts
- Live migration for maintenance (kernel updates without guest downtime)
- HA restart of selected guests after a node failure
- Portfolio value: a documented cluster build with quorum design, replication, and backup strategy is a recognizable production pattern

Non-goals for now: Ceph on bare metal, HA for everything, 10GbE.

---

## 2. Current state (2026-10-15)

All three nodes run `pve-manager 9.2.21`; none are clustered (`/etc/pve/corosync.conf` absent on all).

| Node | CPU | RAM | Boot/guest disk | MGMT IP | Spare NIC | Role |
|---|---|---|---|---|---|---|
| swearengen | i5-10600K (Comet Lake, 6c/12t) | 48 GB (~9 GB free) | Samsung 980 PRO 2TB, LVM-thin | 10.79.10.5 | `enp4s0f1` (igb) | Primary: TrueNAS, HAOS, Docker, Plex, dev/lab VMs |
| wu | Celeron N5105 (Jasper Lake, 4c/4t) | 32 GB | WD SN850X 2TB, LVM-thin | 10.79.10.10 | none (`enp1s0` = WAN) | OPNsense, pihole2, utter, dority |
| garrett | i7-7700K (Kaby Lake, 4c/8t) | 16 GB | Intel 660p 1TB, LVM-thin + 2× unused Crucial C300 128GB SATA | 10.79.10.25 | `nic0` (igb) | k3s lab, Icinga2, Postgres, nested Ceph lab |

All three use a VLAN-aware `vmbr0` with matching names, and the storage IDs `local` / `local-lvm` match on every node. Both of those help.

---

## 3. Constraints found during review

### 3.1 Router lives on a candidate node
OPNsense runs on wu. In a cluster:
- **Losing quorum** makes `/etc/pve` read-only on that node. Running guests keep running, but you can't start, stop, or edit them.
- **With HA enabled**, a node that loses quorum self-fences through the watchdog. If wu fences, the internet goes down.

**Rule:** OPNsense is never an HA resource. Whether wu joins at all is an open decision (§5).

### 3.2 VMID collision
VMID 1000 exists on both swearengen (`truenas`) and wu (`pihole2`). VMIDs must be unique cluster-wide.
**Fix:** Renumber pihole2 into the 100–199 infra range, which matches the VMID scheme anyway.

### 3.3 Joining nodes must have no guests
Only the node that *creates* the cluster may hold guests.
- Create the cluster on **swearengen**, which has the most guests and the passthrough.
- **garrett** guests are Terraform-managed. Tear them down, join, then re-apply. Other VMIDs don't collide with swearengen.
- **wu** requires an OPNsense outage for backup, join, and restore.

### 3.4 CPU baseline
The N5105 (Tremont) has no AVX, so `x86-64-v2-AES` is the highest model common to all three nodes. swearengen + garrett alone could share `x86-64-v3`.
- Guests meant to migrate: set them to the common model.
- Pinned guests stay on `cpu: host`: truenas, bmclab, gamedev01.

### 3.5 No shared or replicable storage
All nodes use LVM-thin, and Proxmox storage replication requires ZFS. Live migration with local disks works, but it copies the disk over 1GbE every time.

| Option | Verdict |
|---|---|
| Ceph on leftover NVMe space | Rejected. Single disk per node, 1GbE, N5105 node, ⅓ usable capacity. Keep Ceph in the nested lab on garrett. |
| NFS from TrueNAS | Rejected for guest disks. TrueNAS is itself a guest on the cluster, which is circular. |
| **Local ZFS + storage replication** | **Chosen.** Async replication every N minutes. On node failure, HA restarts the guest from the last replica, losing at most one interval. |

This requires reinstalling nodes on ZFS, which wipes the NVMe, so a full backup comes first (§6).

### 3.6 Pinned guests (cannot migrate regardless)

| Guest | Pinned by | Mitigation |
|---|---|---|
| truenas (1000) | SATA controller passthrough `00:17.0` | None; it stays on swearengen |
| farnum (301) | `/dev/dri` bind mounts and host NFS mount `/nfs/swearengen` | Could replicate the mount and iGPU config on another node; low priority |
| dority (110) | USB passthrough of the UPS | Moving to the ODROID-C5 NUT server, which removes the pin |
| opnsense (100) | WAN on `vmbr1` (wu `enp1s0`) | See §4.4 |

### 3.7 Housekeeping before any migration
- Detach ISOs: `hearst` (FreeBSD bootonly) and `opnsense` (24.7 DVD) still reference `local:iso/...`.
- Reboot each node into the installed `7.0.14-24` kernel. The running kernels currently differ (-19, -15, -17).
- Add `dns-search greenbean.org` to garrett's `vmbr0`.
- Confirm that `/etc/hosts` on each node resolves every node's hostname to its 10.79.10.x address.
- Note `vm-1000-disk-1` (TrueNAS boot) at 98% thin usage and `vm-301-disk-0` (farnum rootfs) at 96%. Check inside the guests.

### 3.8 Capacity
The cluster has about 96 GB RAM in total, and swearengen is already near full. There is **no N+1 for swearengen**. HA is for small, critical guests only, not an evacuation plan.

---

## 4. Target design (draft)

### 4.1 Membership
- **Core:** swearengen + garrett.
- **Third vote:** either wu as a full member or a corosync QDevice (`corosync-qnetd`) on the ODROID-C5 or pi2b. See §5.

### 4.2 Corosync networks
- **link0:** MGMT VLAN 10 on `vmbr0` (10.79.10.0/24).
- **link1:** direct cable between swearengen `enp4s0f1` and garrett `nic0` on a dedicated /30 (e.g. 172.30.99.0/30), with no switch in the path.
- Corosync addresses are painful to change later, so finalize IPs before creating the cluster.

### 4.3 Storage
- Reinstall swearengen and garrett on ZFS (`rpool`).
- garrett option: mirror the two C300 SSDs as boot and use the whole 660p as a separate guest pool.
- Pool names must match on every node that participates in replication.
- Replication jobs only for guests that are HA candidates.

### 4.4 Making OPNsense movable (optional)
If the WAN handoff comes from the MikroTik `bridge-wan` as copper:
1. Add a second port to `bridge-wan`, keeping it as its own bridge with no VLAN filtering per the existing hardware requirement.
2. Cable that port to a spare NIC on another node and create an identical `vmbr1` there.
3. OPNsense can then migrate manually. The cloned BGW320 WAN MAC lives in the VM config, so it moves with it.

OPNsense remains **manual migration only**, never HA.

**Fallback router:** the Ubiquiti EdgeRouter Lite.
- Configure it once with the VLANs and DHCP, then keep it on the shelf for emergencies.
- It's not a permanent replacement: WireGuard is a third-party package, there's no os-nut, the firmware is effectively at end of line, and throughput depends on hardware offload staying engaged.

### 4.5 HA candidates (first pass)

| Guest | HA? | Notes |
|---|---|---|
| nuttal (HAOS) | Maybe | Benefits from automatic restart |
| ellsworth (Kuma/Docker) | Yes | Monitoring should survive a node loss; set the CPU to the common model |
| pihole (renumbered) | Yes, if it moves to the cluster | The primary Pi-hole stays on the XU4, so this provides DNS diversity |
| truenas, farnum, opnsense | No | Pinned |
| lab guests | No | Disposable |

---

## 5. Open decisions

- [ ] **wu: join or stay standalone?**
  - Joining gives a real 3-node quorum and one UI.
  - Staying standalone keeps the router out of corosync and avoids the N5105 CPU floor. It would then be managed through Proxmox Datacenter Manager instead.
- [ ] **Third vote:** wu vs QDevice on the ODROID-C5 / pi2b.
- [ ] Replication interval (e.g. 5 vs 15 min) vs 1GbE link load.
- [ ] Whether farnum's iGPU and NFS setup is worth replicating on garrett.
- [ ] WAN provider (AT&T 300/300 vs Wire 3 1000/1000). This changes the `bridge-wan` and ONT-stick picture.

---

## 6. Backup target — Proxmox Backup Server

### 6.1 Migration-time backups (no new hardware required)
- vzdump every guest to NFS on TrueNAS `tank`, which has ~18 TB free on passthrough disks unaffected by the NVMe wipe.
- Export the TrueNAS config file. After the swearengen reinstall, rebuild the TrueNAS VM, re-pass `00:17.0`, import the config, and the pool comes back intact.
- During the swearengen reinstall, tank holds the only copy of swearengen's guests. Get a second copy onto the PBS box before wiping.

### 6.2 Long-term PBS box
**Hardware (to verify):**
- Supermicro 1U, believed to be an SC813M chassis (4× 3.5" bays).
- Probably a dual-socket Xeon on DDR3 (X8DT* or X9DR* class), but unconfirmed.
- Disks: a box of used 2 TB spinning drives.

**Before trusting it:**
- [ ] Confirm the board: `dmidecode -t baseboard`, the BMC web UI, or `ipmitool fru`.
- [ ] Run memtest and a CPU stress test.
- [ ] Run a SMART long test plus `badblocks -wsv` on each candidate 2 TB disk, and discard any with reallocated or pending sectors.

**Build:**
- Install PBS with ZFS raidz1 across 4× 2 TB (≈6 TB usable before dedup). Use ZFS rather than mdadm RAID5, to get checksums and scrubs on aging disks.
- Run it on demand, not 24/7, because it's loud and power-hungry. Power it on through IPMI (`ipmitool chassis power on`), run backup, verify and prune jobs, then power it off.
- Offsite sync is a later item. Note that kk1 (Oracle free tier) storage is limited.

---

## 7. Phased plan (draft order)

1. **Prep**
   - Renumber pihole2 and detach the ISOs.
   - Reboot all nodes into the current kernel and fix DNS search and `/etc/hosts`.
   - Move the UPS to the ODROID-C5 NUT server.
2. **PBS**
   - Validate the Supermicro and disks, then install PBS.
   - Back up all guests to PBS, plus vzdump to tank.
   - Test at least one restore.
3. **garrett → ZFS**
   - Reinstall garrett on ZFS.
   - Restore its guests or re-apply Terraform.
4. **swearengen → ZFS**
   - Export the TrueNAS config, then reinstall swearengen on ZFS.
   - Rebuild TrueNAS and import the pool.
   - Restore the remaining guests.
5. **Create the cluster**
   - Create the cluster on swearengen with both corosync links.
   - Empty garrett and join it.
   - Add the third vote (wu or QDevice).
6. **Replication + HA**
   - Set migrate-capable guests to the common CPU model.
   - Create replication jobs.
   - Add HA resources one at a time, and test failover by pulling a node's power.
7. **Optional:** add the second `bridge-wan` port and `vmbr1` to make OPNsense movable.

---

## 8. Risks / gotchas to remember

- Never make OPNsense an HA resource (§3.1).
- A node that loses quorum with HA active **will reboot itself**.
- Get the corosync IPs right the first time.
- tank is a single point of truth during phase 4.
- Old 2 TB disks: a raidz1 rebuild is when the second drive dies. PBS is a copy, not the only copy.
- Replication is async. An HA failover loses data written since the last sync.

---

## 9. Changelog

- 2026-10-15 — Initial theorycraft draft from host inventory dumps of swearengen, wu, garrett
