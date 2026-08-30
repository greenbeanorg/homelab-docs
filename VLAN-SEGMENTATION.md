# VLAN SEGMENTATION

Migration of greenbean.org from a flat `10.x.x.N/24` to a segmented VLAN
network on `10.79.x.x`. Covers the CRS310, a new TL-SG108E access switch,
OPNsense, both Proxmox hosts, and the EAP610 AP.

Built 2026-08-30. Status: **partially complete** — see [Remaining Work](#remaining-work).

---

## Why

Two goals:

1. **Segmentation.** Isolate IoT, guest, and lab devices from servers and
   trusted clients.
2. **Retire `10.x.x.N/24`.** It's one of the most commonly squatted ranges on
   consumer and hotel networks, which breaks WireGuard `AllowedIPs` when
   roaming. `10.79.x.x` was chosen because it avoids Docker's default pools
   (172.17+), the WG overlay (10.99), kk1's Oracle VCN (10.0.179), and the
   ranges commercial VPN clients grab (10.6/10.7/10.8).

Renumbering is not extra work — every new VLAN needs a new subnet anyway.
`10.x.x.N/24` gets *drained* rather than migrated, and deleted when the last
service leaves.

---

## VLAN map

| ID  | Name     | Subnet          | Gateway      | Contents                          |
|-----|----------|-----------------|--------------|-----------------------------------|
| 10  | MGMT     | (still flat)    | 10.x.x.N     | OPNsense, both Pi-holes, switches, AP, Proxmox hosts |
| 20  | SERVERS  | 10.x.x.N/24   | 10.x.x.N   | truenas, farnum, ellsworth, nuttal, hickok, dority |
| 30  | CLIENTS  | 10.x.x.N/24   | 10.x.x.N   | desktop, Mac mini, printer, Roku, household wifi |
| 40  | IOT      | 10.x.x.N/24   | 10.x.x.N   | Yolink hub                        |
| 50  | GUEST    | 10.x.x.N/24   | 10.x.x.N   | guest wifi (not yet deployed)     |
| 60  | LAB      | 10.x.x.N/24   | 10.x.x.N   | sol                               |
| 999 | WAN      | —               | —            | **abandoned** — see WAN section   |

Host numbering within each /24:

| Range      | Use                          |
|------------|------------------------------|
| .1         | OPNsense gateway             |
| .2–.9      | Network infra                |
| .10–.49    | Static hosts                 |
| .50–.99    | DHCP reservations            |
| .100–.199  | DHCP pool                    |
| .200–.254  | Scratch / unallocated        |

---

## Physical topology

```
ONT (SFP+, 8311 fw)  ──┐
                       ├── bridge-wan (ISOLATED, protocol-mode=none)
CRS310 ether1 ─────────┘         │
                                 └── wu enp1s0 → vmbr1 → OPNsense WAN (vtnet1)

CRS310 bridge (vlan-filtering=yes)
  ether3  desktop           PVID 30
  ether4  odroidxu4/pihole  PVID 10
  ether5  RESCUE PORT       PVID 10
  ether6  Mac mini          PVID 30
  ether7  wu                PVID 10 + tagged 20,30,40,50,60
  ether8  sunroom run       PVID 10 + tagged 20,30,40,50,60
             │
             └── TL-SG108E port 1 (trunk)
                   port 2  swearengen  (trunk, all VLANs)
                   port 3  EAP610      (tagged 30,40,50)
                   port 4  Yolink      PVID 40
                   port 5  HP printer  PVID 30
                   port 6  Roku        PVID 30
                   port 7-8 spare      PVID 1
```

---

## CRS310 (RouterOS 7.22.2)

Applied config. **Note VLAN 10 is untagged on ether7/ether8** — see gotcha #3.

```
/interface ethernet
set [find default-name=ether1] comment="WAN Ethernet"
set [find default-name=ether2] comment="SPARE - stuck PHY 2026-08-30, recovered after reboot"
set [find default-name=ether3] comment="Desktop (dual-boot Win/Linux)"
set [find default-name=ether4] comment="odroidxu4 / pihole"
set [find default-name=ether5] comment="RESCUE PORT - mgmt access, keep free"
set [find default-name=ether6] comment="Mac mini M4"
set [find default-name=ether7] comment="wu (Proxmox) - incl. OPNsense VM"
set [find default-name=ether8] comment="sunroom run - outdoor ethernet run"
set [find default-name=sfp-sfpplus1] comment="WAN Fiber"

/interface bridge port set [find interface=ether3] pvid=30 comment="Desktop - CLIENTS native"
/interface bridge port set [find interface=ether4] pvid=10 comment="pihole - MGMT native"
/interface bridge port set [find interface=ether5] pvid=10 comment="RESCUE PORT - MGMT native"
/interface bridge port set [find interface=ether6] pvid=30 comment="Mac mini - CLIENTS native"
/interface bridge port set [find interface=ether7] pvid=10 comment="wu - MGMT native, trunk"
/interface bridge port set [find interface=ether8] pvid=10 comment="sunroom trunk - MGMT native"

/interface bridge vlan
add bridge=bridge vlan-ids=10 tagged=bridge untagged=ether4,ether5,ether7,ether8 comment="MGMT"
add bridge=bridge vlan-ids=20 tagged=ether7,ether8 comment="SERVERS"
add bridge=bridge vlan-ids=30 tagged=ether7,ether8 untagged=ether3,ether6 comment="CLIENTS"
add bridge=bridge vlan-ids=40 tagged=ether7,ether8 comment="IOT"
add bridge=bridge vlan-ids=50 tagged=ether7,ether8 comment="GUEST"
add bridge=bridge vlan-ids=60 tagged=ether7,ether8 comment="LAB"

/interface vlan add interface=bridge name=vlan10-mgmt vlan-id=10
/ip address add address=10.x.x.N/24 interface=vlan10-mgmt

/interface bridge set bridge vlan-filtering=yes
```

The legacy `10.x.x.N/24` on bare `bridge` was **left in place** as a fallback.
Remove it only after the MGMT renumber.

### Verifying

```
/interface bridge vlan print detail
```

`current-tagged` / `current-untagged` populate only when `vlan-filtering=yes`.
Empty values there while filtering is on = the table isn't being enforced.

### Rollback

```
/system scheduler add name=vlan-rollback interval=5m \
  on-event="/interface bridge set bridge vlan-filtering=no"
```

Arm this *before* flipping filtering. Delete it once verified.

---

## WAN — do not fold into the main bridge

`bridge-wan` stays a **separate bridge with `protocol-mode=none`**, holding
only `ether1` and `sfp-sfpplus1`, with `vlan-filtering=no`.

```
/interface bridge add name=bridge-wan protocol-mode=none
/interface bridge port add bridge=bridge-wan interface=sfp-sfpplus1 edge=yes point-to-point=yes
/interface bridge port add bridge=bridge-wan interface=ether1 edge=yes point-to-point=yes
```

### Why this is not negotiable

An attempt was made to fold both WAN ports into the main bridge on an isolated
VLAN 999, to recover hardware offloading (WAN was showing ~64% CPU in IRQ under
load, because the second bridge doesn't get switch-chip offload — every WAN
packet hits the ARM CPU).

**It broke the ONT.** The 8311 firmware needs a clean L2 path: no spanning
tree, no VLAN awareness, no other ports in the domain. After the fold-in the
ONT reached PON state O5 but never got a DHCP lease, and OPNsense's WAN sat
with no address for roughly an hour.

The CPU cost is the price of the setup working. Do not revisit this.

**Diagnostic value:** compare `driver-rx-packet` against `rx-unicast` in
`/interface ethernet print stats`. Hardware-forwarded ports show ~0.1% of
packets reaching the CPU. WAN ports show 100%.

---

## TL-SG108E

Management: **10.x.x.N** (was DHCP — see gotcha #5).

802.1Q VLAN table:

| VLAN | Tagged      | Untagged |
|------|-------------|----------|
| 1    | —           | 1–8      |
| 20   | 1, 2        | —        |
| 30   | 1, 2, 3     | 5, 6     |
| 40   | 1, 2, 3     | 4        |
| 50   | 1, 2, 3     | —        |
| 60   | 1, 2        | —        |

PVIDs: port 4 → 40, ports 5–6 → 30, all others → 1.

Notes:

- **VLAN membership and PVID are separate pages.** Setting one without the
  other is the most common misconfiguration.
- **Save Config** in the left nav or changes are lost on power cycle.
- Firmware `1.0.0 Build 20250710` (HW v6.0) is newer than anything on TP-Link's
  download page. Do not "upgrade."
- No usable config export. Screenshot the VLAN and PVID pages for backup.
- Port 2 (swearengen) is a full trunk on all VLANs rather than just its current
  tenants — a Proxmox host shouldn't need a switch visit every time a VM lands
  in a new segment.

---

## OPNsense

**Interfaces → Other Types → VLAN**, parent `vtnet0` (LAN):

| Device  | Tag | Description | Address        |
|---------|-----|-------------|----------------|
| vlan01  | 20  | SERVERS     | 10.x.x.N/24  |
| vlan02  | 30  | CLIENTS     | 10.x.x.N/24  |
| vlan03  | 40  | IOT         | 10.x.x.N/24  |
| vlan04  | 50  | GUEST       | 10.x.x.N/24  |
| vlan05  | 60  | LAB         | 10.x.x.N/24  |

Assigned as opt1–opt5, renamed to match. Leave "Block private networks"
unchecked — that's for WAN-facing interfaces and would block legitimate
RFC1918 traffic here.

### Kea DHCPv4

One subnet per interface, pool `.100–.199`, all referencing a shared
**DNS_1** option (`domain-name-servers = 10.x.x.N, 10.x.x.N`). Using one
shared option object rather than per-subnet entries means the Pi-hole renumber
is a one-line change later.

**Auto collect option data** stays checked — Kea derives the router option
from the interface IP. Only DNS needs the explicit option, because auto-collect
would point at OPNsense's own Unbound, which is disabled here.

⚠️ **Kea must be bound to each interface** under Kea DHCPv4 → Settings →
Interfaces. Defining a subnet is not enough. This was the cause of the wifi
DHCP failure at the end of the day.

### Firewall rules

**SERVERS** — one rule, `SERVERS net → any` (Uptime Kuma needs to poll
everything).

**CLIENTS**:
1. `CLIENTS net → 10.x.x.N : domain` (TCP/UDP)
2. `CLIENTS net → 10.x.x.N : domain`
3. `CLIENTS net → SERVERS net : any`
4. `CLIENTS net → any : any`

**IOT / GUEST / LAB** — identical shape, order matters:
1. `→ 10.x.x.N : domain`
2. `→ 10.x.x.N : domain`
3. **Block** `→ 10.x.x.N/8`
4. **Block** `→ 10.x.x.0/12`
5. **Block** `→ 10.x.x.N/16`
6. Pass `→ any` (internet only, since RFC1918 already blocked above)

The RFC1918 blocks must sit **above** the broad pass, or first-match evaluation
fires the pass and the blocks never run.

No built-in RFC1918 alias existed on this version — the three CIDRs were
entered manually.

### Verifying rules via API

The GUI hides field values that matter. Read them directly:

```bash
curl -sk -u "key:secret" https://10.x.x.N/api/firewall/filter/searchRule | python3 -m json.tool
curl -sk -u "key:secret" https://10.x.x.N/api/kea/dhcpv4/searchSubnet | python3 -m json.tool
curl -sk -u "key:secret" https://10.x.x.N/api/interfaces/vlan_settings/searchItem | python3 -m json.tool
```

Key from System → Access → Users → API keys. Revoke when done.

This caught a real bug: the CLIENTS "Internet" rule had destination
**"WAN address"** instead of **any**. That alias means OPNsense's own WAN IP,
not the internet — clients could resolve DNS and reach SERVERS but not browse.
Invisible in the GUI, obvious in the JSON.

---

## Proxmox — wu and swearengen

Both hosts need VLAN-aware bridges:

```
auto vmbr0
iface vmbr0 inet static
        address 10.x.x.N/24          # 10.x.x.N on swearengen
        gateway 10.x.x.N
        bridge-ports enp2s0           # enp4s0f0 on swearengen
        bridge-stp off
        bridge-fd 0
        bridge-vlan-aware yes
        bridge-vids 2-4094
```

Apply from **console** (`systemctl restart networking` drops the network you'd
be SSH'd over). On wu this drops OPNsense, so the whole house goes offline for
the duration.

### Guest interface rules

Once the bridge is VLAN-aware, **every guest port defaults to VLAN 1 only.**
The host's own IP is the exception — it rides the bridge interface itself and
comes out untagged.

| Guest type | Setting |
|------------|---------|
| Router VM (OPNsense) | `trunks=2-4094` — needs all VLANs to build its own sub-interfaces |
| Single-VLAN VM | `tag=20` (or whichever) |
| Guest on MGMT (pihole2) | **no tag at all** — rides untagged, gets PVID 10 at ether7 |

```
qm set 100 -net0 virtio=<MAC>,bridge=vmbr0,trunks=2-4094
qm set 110 -net0 virtio=<MAC>,bridge=vmbr0,tag=20
pct set 1000 -net0 name=eth0,bridge=vmbr0,firewall=1,gw=10.x.x.N,hwaddr=<MAC>,ip=10.x.x.N/24,type=veth
```

⚠️ `qm set -net0` **replaces the entire parameter.** Copy the existing value
from `qm config <vmid>` first — `firewall=1` was silently dropped this way.

---

## EAP610

`Wireless → VLAN` — per-SSID, **per-band** (2.4 and 5GHz are separate rows and
must match). Standalone mode, no Omada controller needed.

Firmware `1.6.0 Build 20250507`.

Test with a new SSID before tagging `gtf0` — tagging the production SSID moves
every wireless client in the house at once.

---

## Gotchas

### 1. Safe Mode reverts everything when your SSH session drops

RouterOS Safe Mode (Ctrl+X) auto-reverts on connection loss. When you're SSH'd
in over the bridge you're reconfiguring, flipping `vlan-filtering=yes` drops
the session, and Safe Mode dutifully undoes the entire change. This happened
**three times** before it was recognised — each time looking like "the paste
didn't work."

Symptoms: config verified correct, then `/interface bridge print` shows
baseline state and a fresh login banner mid-paste. Uptime unchanged (not a
reboot).

Fix: either use true console access, or accept the session drop and rely on
the scheduler rollback instead of Safe Mode.

### 2. A VLAN-aware Linux bridge does NOT transparently pass tags

Early assumption: wu needed no changes because a plain Linux bridge forwards
802.1Q frames. **Wrong.** OPNsense's VLAN sub-interfaces never received tagged
traffic until vmbr0 was made VLAN-aware *and* the vNIC got `trunks=2-4094`.

Both halves are required. VLAN-aware bridge alone leaves every guest port on
VLAN 1.

### 3. VLAN 10 must be UNTAGGED on the trunks to wu and swearengen

Originally VLAN 10 was tagged on ether7/ether8. Neither Proxmox host strips a
VLAN 10 tag — their management IPs live on the bare bridge interface and expect
untagged frames. Result: wu couldn't reach swearengen, the desktop couldn't
reach its gateway, nothing worked despite a correct-looking table.

```
/interface bridge vlan set [find vlan-ids=10] untagged=ether4,ether5,ether7,ether8 tagged=bridge
```

`bridge` stays tagged so the CRS310's own `vlan10-mgmt` interface works.
Omitting `bridge` from VLAN 10's tagged list is the classic MikroTik lockout.

### 4. `firewall=1` moves where the VLAN tag lands

With the Proxmox firewall enabled on a vNIC, the guest doesn't attach to vmbr0
directly. Proxmox inserts a chain:

```
veth1000i0 → fwbr1000i0 → fwpr1000p0 → vmbr0
```

The VLAN tag lands on the **`fwpr` leg**, not the `tap`/`veth`. So
`bridge vlan show` showing `veth1000i0  1 PVID` does *not* mean the tag failed
— check the corresponding `fwpr` line.

This wasted significant time. Run `bridge vlan show` in full; grepping for
`veth`/`tap` hides the whole story, and grepping any port hides its
continuation lines (additional VIDs print without the port name).

### 5. The SG108E was on DHCP, not 10.x.x.N

Hours were spent trying to reach the documented default via a USB dongle on
various ports. It had pulled `10.x.x.N` from the LAN pool and was reachable
from the normal network the entire time.

Find it by MAC (`<MAC>`) in the Kea leases or ARP table. Then set
it static immediately.

### 6. Onboard SATA passthrough blocks VM restart

TrueNAS (VMID 1000) passes through the Comet Lake SATA controller at
`00:17.0`, which has **no FLR reset mechanism**:

```
kvm: vfio: Cannot reset device 0000:00:17.0, no available reset mechanism.
```

Any config change requiring a TrueNAS restart means a **swearengen host
reboot**. Batch such changes.

### 7. Tag and renumber must happen together

Tagging a VM into VLAN 20 while it still holds a `10.0.0.x` address strands it
— no gateway on that segment. Renumbering to `10.79.20.x` without the tag
strands it equally. Change the guest OS first, then the vNIC tag, then restart.

### 8. Stuck PHY looks exactly like a dead port

ether2 showed `no-link` across three cables and two devices. Confirmed dead —
until a reboot brought it back at full speed. PHYs can hang into a state that
only a power cycle clears, on brand-new hardware as readily as old. Always
reboot before filing an RMA.

### 9. AT&T holds the session after an unclean disconnect

After the WAN outage, the ONT reached O5 but no lease appeared for roughly an
hour, then recovered on its own with no intervention. Repeatedly power-cycling
may reset their timer. Leave it connected and wait.

---

## Remaining work

- [ ] **swearengen VM tags** — farnum, ellsworth, nuttal, hickok, truenas →
      `tag=20`. Guest OSes already renumbered to `10.79.20.x`. Requires a host
      reboot because of the TrueNAS passthrough.
- [ ] **EAP610** — `t3st1ng` verification after the Kea interface binding fix;
      then move `gtf0` to VLAN 30 and add a guest SSID on VLAN 50.
- [ ] **Move sunroom clients** — printer to port 5, Roku to port 6, Yolink to
      port 4.
- [ ] **WireGuard** — both tunnels dropped during the WAN outage; peers may
      need updating if the public IP changed.
- [ ] **MGMT renumber** — OPNsense to 10.x.x.N, both Pi-holes, CRS310 (drop
      `10.x.x.N`), SG108E, EAP610, both Proxmox hosts. Then update the DNS_1
      Kea option, the ~20 A records, WireGuard `AllowedIPs` on kk1 and every
      spoke, restic config, and `inventory.yaml`.
- [ ] **Plex LAN Networks** — add the new client subnets or farnum will treat
      local clients as remote and transcode.
- [ ] **Avahi** — needed for mDNS across VLAN boundaries (casting, printer
      discovery).
- [ ] **Uptime Kuma** — move to ellsworth, update `inventory.yaml` for the new
      addresses.
- [ ] **Check `scripts/pre-commit`** — if the sanitizer regex is hardcoded to
      `10\.0\.0\.` rather than matching RFC1918 generally, it will silently
      stop masking after the renumber.
- [ ] **Delete stale hickok** on wu (VMID 102) — migration to swearengen (202)
      is complete.

---

## Backups

Pre-change configs live at
`/mnt/tank/storage/important/backup/network-backup08-25-2026/`:

- `mikrotik-crs310-8g.txt`
- `config-OPNsense.greenbean.org-*.xml`
- `pi-hole_odroidxu4_teleporter_*.zip`
- `pi-hole_pihole2_teleporter_*.zip`

Post-change exports still needed:

```
/export file=crs310-post-vlan
```

Plus a fresh OPNsense XML (System → Configuration → Backups) and an EAP610
backup (System → Backup & Restore).

---

