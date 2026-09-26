# swearengen vmbr0: "Intra-Bridge Forwarding Bug" — Actually a Guest /32 Netmask

**Status: resolved.** This runbook originally concluded that `vmbr0` on
`swearengen` had a kernel/QEMU/vhost-level forwarding bug affecting one
same-host VM pair. **That conclusion was wrong.** The bridge was never at
fault. `nuttal` (HAOS) had its address configured with a `/32` prefix, so it
treated same-subnet peers as off-link and sent every reply to the gateway
instead of across the bridge. The forward path always worked; only the return
path was asymmetric.

The original elimination process is preserved below, because the negative
results are still valid and the *misreading* of the decisive evidence is the
most useful thing in this document.

---

## 1. Symptom

- `ellsworth` (Docker host, VM 101) cannot reach `nuttal` (Home Assistant OS,
  VM 201) on port 8123 — connections hang and time out.
- Both VMs run on the same Proxmox host, `swearengen`, on the same
  VLAN-aware bridge (`vmbr0`), same VLAN, same subnet.
- ICMP and ARP between the two are always clean — low latency, zero loss,
  correct MAC resolution every time.
- TCP is where it breaks. Every other path to the same service — from
  OPNsense, from a third host on the same VLAN via the physical switch, from
  a VM on a different Proxmox node — succeeds 100% of the time.

⚠️ **The ICMP/TCP split was the tell, and it was misread as "intermittent."**
ICMP tolerates an asymmetric return path: the reply reaches the source
regardless of which way it travelled. TCP does not, because the stateful
firewall in the middle of the return path never saw the SYN and therefore
drops the SYN-ACK as out-of-state. Any time ping works and TCP does not
between two hosts on the same subnet, suspect asymmetric routing before
suspecting the bridge.

---

## 2. Environment

| Item | Value |
| --- | --- |
| Proxmox host | `swearengen` (i5-10600K, 48GB) |
| Bridge | `vmbr0` — VLAN-aware, `bridge-vids 2-4094` |
| VM 101 | `ellsworth` — Fedora, Docker host (Kuma, NetBox, Pulse) |
| VM 201 | `nuttal` — HAOS, Home Assistant Supervisor |
| Both VMs | Same VLAN (20), same `/24`, same physical bridge |
| Kea DHCP scope | `10.x.x.0/24` |

---

## 3. Root Cause

**`nuttal`'s interface was configured as `10.x.x.30/32`.**

A `/32` prefix means the host has no on-link subnet. Every destination —
including `10.x.x.111` on the same bridge, two ports away — is off-link and
resolves to the default gateway. So:

- `ellsworth → nuttal` traffic crossed `vmbr0` directly and arrived normally.
- `nuttal → ellsworth` replies were addressed to the **gateway's** MAC, left
  the host via `enp4s0f0`, were routed by OPNsense, and came back down with
  TTL decremented 64 → 63.

For ICMP that still completes, so ping "worked." For TCP to 8123, OPNsense
never saw the SYN (it went direct across the bridge), so the SYN-ACK arriving
from `nuttal` was out-of-state and dropped. The handshake never completed and
retransmitted until timeout.

The HAOS login banner prints the prefix on every SSH connection
(`IPv4 addresses for enp6s18: 10.x.x.30/32`) — the answer was on screen for the
entire investigation.

### How the original diagnosis went wrong

Step 7 of the original investigation ran `tcpdump` on both taps
simultaneously and observed: SYN-ACK generated and retransmitted on the
destination's tap, never arriving on the source's tap. That observation was
**correct**. The inference — that the frame vanished inside the bridge — was
not. The SYN-ACK left via the uplink, which was the one interface never
captured. Every check was aimed at the two taps, so the traffic that proved
the case was outside the field of view the whole time.

⚠️ **Methodological lesson: when both endpoints look clean, capture the
interface the traffic should *not* be using.** A dual-tap capture can only
show presence and absence on those two ports; it cannot distinguish "dropped
by the bridge" from "correctly forwarded somewhere else." Adding
`tcpdump -e` on the physical uplink settles it in one command, because the
destination MAC and the TTL both identify the path immediately.

---

## 4. Diagnostic Path

### 4.1 The capture that settled it

With the workaround route removed on `ellsworth` and a continuous ping
running:

```bash
bridge monitor fdb &
tcpdump -i enp4s0f0 -e -n -vv 'vlan 20 and host 10.x.x.30'
```

Echo *requests* never appeared on the uplink — they crossed the bridge
correctly. Echo *replies* did appear, addressed to the gateway MAC with
`ttl 64`, immediately followed by the same packet returning from OPNsense with
`ttl 63`. The TCP SYN-ACK from `nuttal:8123` appeared on the uplink too,
retransmitted ~8s later, with no corresponding return frame — OPNsense
dropping it as out-of-state.

`-e` (link-layer headers) is essential. Without it, the destination MAC is
invisible and the capture looks like ordinary traffic.

### 4.2 Bridge state verified clean first

Before the uplink capture, the bridge itself was re-verified end to end:

```bash
qm config 101 | grep -E '^net'          # tag=20, no firewall=1
qm config 201 | grep -E '^net'          # tag=20, no firewall=1
ip -br link show master vmbr0           # both taps direct on vmbr0, no fwbr/fwpr
bridge -d link show dev tap101i0        # isolated off, flood on, learning on
bridge vlan show                        # both taps: 20 PVID Egress Untagged
bridge fdb show br vmbr0                # both MACs on own tap, vlan 20
sysctl net.bridge.bridge-nf-call-iptables   # 0
```

All clean. That result is what forced the question "if the bridge is correct,
where is the frame actually going?" — which is the question that should have
been asked two days earlier.

⚠️ Note that `fwpr1000p0` and `fwpr102p0` on this host *do* carry the VLAN tag
on the fwpr leg while their taps sit on VLAN 1 — the documented `firewall=1`
gotcha is real and live on `swearengen`, just not on VMs 101/201. Confirming a
known gotcha applies elsewhere is not evidence it applies here.

### 4.3 Previously ruled out (still valid)

Docker iptables/nftables · Proxmox per-VM firewall (`firewall=1` / fwbr/fwpr
chains) · global host iptables FORWARD · native nftables
(`proxmox-firewall.service`) · ebtables · `tc` qdiscs/filters · bridge port
isolation · VLAN tagging/PVID mismatch · bridge `flood`/`learning` flags ·
checksum offload · MAC address collision · a physical network loop · FDB
relearning/aging · multiqueue flow hashing · `bridge-nf-call-iptables`

All of these were correctly cleared. None of them were ever the problem, and
none of them could have been — the failure was in guest IP configuration, a
layer above everything in this list.

---

## 5. Fix

On `nuttal`, via the `ha` CLI (Proxmox console → `login`, or the SSH add-on
with Protection mode off):

```bash
ha network info
ha network update enp6s18 \
  --ipv4-method static \
  --ipv4-address 10.x.x.30/24 \
  --ipv4-gateway 10.x.x.1 \
  --ipv4-nameserver 10.x.x.250 \
  --ipv4-nameserver 10.x.x.249
```

⚠️ The "Advanced SSH & Web Terminal" add-on drops you into a **container**,
not the HAOS host. `ip addr` there shows the Supervisor's internal Docker
network (`172.30.x.x`) and tells you nothing about the real interface. Use the
banner, `ha network info`, or the Proxmox console.

### Workaround removed

The original host route on `ellsworth` is no longer needed and has been
removed, including the persistent NetworkManager form:

```bash
nmcli connection modify "Wired connection" -ipv4.routes "10.x.x.30/32 10.x.x.1"
nmcli connection up "Wired connection"
```

⚠️ **Why the workaround "worked" is worth understanding.** Forcing
`ellsworth`'s outbound traffic through OPNsense made the path *symmetric* —
both directions then transited the firewall, so state matched and TCP
completed. It did not bypass a bridge fault; it compensated for a guest
misconfiguration by making both halves equally wrong. A workaround that
succeeds for a reason you have not identified is not confirmation of the
diagnosis behind it.

---

## 6. Verification

1. On `ellsworth`, confirm no host route to `nuttal` remains (`ip route get`).
2. `ping` `nuttal` and capture on `enp4s0f0` — replies should no longer appear
   on the uplink at all.
3. `curl -I http://10.x.x.30:8123` from `ellsworth` — should return promptly.
4. Confirm `ellsworth`'s own prefix is a `/24` (`ip -4 addr show`); the same
   misconfiguration class should be ruled out on both ends.

---

## 7. Outstanding / Follow-up

- [ ] Audit prefix length on every static-addressed guest in the fleet — a
      `/32` survives reboots and package updates silently, and nothing in the
      monitoring stack currently detects it.
- [ ] Add a prefix-length assertion to `inventory.yaml`-driven tooling so a
      mismatch between declared and actual netmask is caught automatically.
- [ ] Re-check any other host that received a static address during the
      flat-subnet → VLAN renumber, which is when this was most likely
      introduced.

---

## 8. Known Limitations

- The root cause was introduced by hand during static addressing and is not
  prevented by anything structural. Until the inventory tooling asserts prefix
  length, the same mistake can recur on any new guest.
- HAOS network configuration is only reachable via `ha network` or the
  Supervisor UI; it is not visible in the Proxmox guest config, so a host-side
  audit will not catch it.

---

## Quick Reference

| Check | Command |
| --- | --- |
| **Same-subnet peers unreachable — check this first** | `ip -4 addr show` on both ends; confirm prefix is `/24`, not `/32` |
| Identify asymmetric return path | `tcpdump -i <uplink> -e -n host <peer>` — look for gateway MAC + TTL decrement |
| HAOS interface config (host, not add-on container) | `ha network info` |
| Set HAOS static address correctly | `ha network update <iface> --ipv4-method static --ipv4-address <ip>/24 --ipv4-gateway <gw>` |
| Watch bridge FDB for a MAC live | `watch -n 0.2 'bridge fdb show br vmbr0 \| grep <mac>'` |
| Watch FDB port moves in real time | `bridge monitor fdb` |
| Check VLAN-aware bridge port flags | `bridge -d link show dev <tap>` |
| Check VLAN membership per tap | `bridge vlan show` |
| Check which interfaces are really on the bridge | `ip -br link show master vmbr0` |
| Check Proxmox per-VM firewall | `cat /etc/pve/firewall/<vmid>.fw` |
| Check bridge-nf-iptables interaction | `sysctl net.bridge.bridge-nf-call-iptables` |

---
