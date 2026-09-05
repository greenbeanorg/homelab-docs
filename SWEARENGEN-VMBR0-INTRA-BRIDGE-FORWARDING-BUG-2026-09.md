# swearengen vmbr0: Same-Host Intra-Bridge Forwarding Bug (ellsworth ↔ nuttal)

**Status: workaround in place; root cause unresolved.** A specific VM-to-VM TCP
flow on `swearengen`'s `vmbr0` reliably fails when both VMs share the host and
the traffic never leaves the bridge — while every path that transits a physical
NIC (through OPNsense, from another host, from a VM on a different Proxmox
node) works without exception. This document records the elimination process
in full because the failure signature is unusual enough to be worth recognizing
quickly next time, even though the actual mechanism was never found.

---

## 1. Symptom

- `ellsworth` (Docker host, VM 101) cannot reach `nuttal` (Home Assistant OS,
  VM 201) on port 8123 — connections hang and time out.
- Both VMs run on the same Proxmox host, `swearengen`, on the same
  VLAN-aware bridge (`vmbr0`), same VLAN, same subnet.
- ICMP and ARP between the two are always clean — low latency, zero loss,
  correct MAC resolution every time.
- TCP is where it breaks, and only *some* TCP flows to `nuttal:8123`. Every
  other path to the same service — from OPNsense, from a third host on the
  same VLAN via the physical switch, from a VM on a different Proxmox node —
  succeeds 100% of the time, every test, across two days of testing.
- Intermittent in the sense that a given moment might work, but the specific
  ellsworth→nuttal same-host path never became reliable.

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

## 3. Diagnostic Path (condensed)

The investigation moved outward from the application layer to the packet
layer, then across every filtering/forwarding subsystem on both the source and
destination host, then out to the physical network, and back. In rough order:

1. **HTTP/TCP layer** — `curl` to `nuttal:8123` from `ellsworth` timed out;
   from every other host, it succeeded and returned a normal 200/405 from HA.
2. **Firewall stacks on both VMs and the host** — checked and cleared, in this
   order: Proxmox per-VM firewall (`firewall=1` / `fwbr`/`fwpr` chains),
   `firewalld`'s native nftables ruleset on `ellsworth`, the host's own
   `iptables`/`ip6tables` (`FORWARD` policy was `ACCEPT`, no relevant rules),
   Docker's `DOCKER-FORWARD`/`DOCKER-USER` chains, native `nftables` on
   `swearengen` (empty ruleset — `proxmox-firewall.service` had nothing
   configured), and `ebtables` (empty on both hosts).
3. **Bridge-layer mechanics** — checked `tc` qdiscs/filters on both taps
   (clean, default `fq_codel`), port isolation flags (`isolated off` on both),
   and VLAN membership/PVID on both taps (`bridge vlan show` — identical,
   correct config on `tap101i0` and `tap201i0`).
4. **Offload/checksum** — `rx-checksumming: off [fixed]` / `tx-checksumming`
   variants were identical on both taps and `vmbr0`; forcing `tx off` on
   either tap made no difference.
5. **MAC/FDB state** — checked for a duplicate MAC across VMs (none), then
   watched `bridge fdb show` continuously across multiple curl-failure windows
   for both the source and — critically — the **destination** MAC (forwarding
   is decided by destination MAC, not source; this was an early mistake in
   the investigation that cost significant time). FDB entries for both VMs
   stayed correctly pinned to their own taps through every window captured,
   including windows where curls from other vantage points were succeeding
   and failing normally.
6. **Physical network** — pulled the MikroTik's own FDB entry for both MACs
   (learned correctly on the expected trunk port), checked the access switch
   (TP-Link TL-SG108E) for STP/loop prevention (Loop Prevention was enabled;
   port statistics showed no anomalies), and physically unplugged every
   non-essential cable on that switch one at a time while retesting — no
   change.
7. **Direct packet capture, both taps simultaneously** — this was the
   decisive test, repeated successfully on two separate days with two
   different MACs (post NIC-rebuild) on `ellsworth`. `tcpdump` on `tap201i0`
   (nuttal's tap) shows the SYN arrive and the SYN-ACK generated and
   retransmitted correctly, every time. Simultaneous `tcpdump` on `tap101i0`
   (ellsworth's tap) never shows that SYN-ACK arrive — not once, across
   dozens of retransmissions. The reply is generated correctly and then
   vanishes somewhere between the two taps on the same bridge.
8. **Multiqueue** — checked as a possible flow-hashing explanation; neither
   VM's `net0` specifies `queues=`, so both are single-queue. Ruled out.

### Ruled out (confirmed, not suspected)
Docker iptables/nftables · Proxmox per-VM firewall · global host iptables
FORWARD · native nftables (`proxmox-firewall.service`) · ebtables · `tc`
qdiscs/filters · port isolation · VLAN tagging/PVID mismatch · checksum
offload · MAC address collision · a physical network loop · FDB
relearning/aging on either endpoint's MAC · multiqueue flow hashing

---

## 4. Root Cause

**Not identified.** Every layer with standard visibility (netfilter in all its
forms, the bridge's own filtering and VLAN state, tc, offload flags, FDB
state, and the physical network) was checked and cleared, on two separate
days, with two different source MACs. The one fact that survived every test:
the SYN-ACK is correctly generated on the destination's tap and never arrives
on the source's tap, despite both being ports on the same Linux bridge with
no filtering rule touching either of them.

This points at something below the layers `tcpdump`, `bridge`, `tc`, `nft`,
and `ethtool` can see — most likely a kernel or QEMU/vhost virtio-net bridging
bug specific to this host's kernel/qemu-server version combination. Root-causing
further would require kernel-level tracing (`ftrace`/`perf`) or a kernel/qemu
version bisect — a materially different order of effort than troubleshooting,
and not undertaken here.

---

## 5. Workaround Applied

Force the affected flow through a physical NIC instead of the local bridge
hairpin, by adding a more-specific host route on `ellsworth` that sends
`nuttal`-bound traffic to OPNsense instead of resolving `nuttal`'s MAC
directly:

```bash
sudo ip route add 10.x.x.30/32 via 10.x.x.1 dev ens18
```

OPNsense hairpins the traffic back onto the same VLAN to reach `nuttal`. Every
OPNsense-routed test succeeded throughout the investigation, so this
reproduces a known-good path rather than introducing a new untested one.

**Made persistent** via the NetworkManager connection profile rather than a
one-off `ip route` command, so it survives reboots:

```bash
nmcli connection modify "Wired connection" +ipv4.routes "10.x.x.30/32 10.x.x.1"
nmcli connection up "Wired connection"
```

---

## 6. Outstanding / Follow-up

- [ ] Revisit if `swearengen`'s kernel or `qemu-server` package is ever
      upgraded — this may be a version-specific virtio-net/bridge bug that
      changes behavior on update.
- [ ] Watch for the same symptom on any *other* same-host VM pair on
      `vmbr0`. If it recurs elsewhere, that's strong evidence this is
      systemic to the bridge/host rather than specific to ellsworth/nuttal,
      and worth escalating to kernel-level tracing.
- [ ] If pursued further: `perf trace` or `ftrace` on the bridge forwarding
      path during a live failure, or a controlled kernel/qemu-server version
      bisect.
- [ ] Update [WIREGUARD-TROUBLESHOOTING.md](WIREGUARD-TROUBLESHOOTING.md) /
      VLAN docs with a cross-reference once VLAN 10 MGMT work is complete, in
      case the same symptom shape appears there.

---

## 7. Known Limitations

- The fix is a routing workaround, not a resolution — the underlying bridge
  behavior is still present and unexplained.
- The workaround is host-route-specific (`ellsworth` → `nuttal` only). If
  other same-host VM pairs hit the same bug, each would need its own route,
  which does not scale cleanly and is a sign this should eventually be
  root-caused properly rather than patched per-pair.
- The static route depends on OPNsense correctly hairpinning intra-VLAN
  traffic indefinitely; if that behavior ever changes (a stricter firewall
  rule, an OPNsense upgrade that disables reflection), the workaround breaks
  silently and would look identical to the original bug reappearing.

---

## Quick Reference

| Check | Command |
| --- | --- |
| Watch bridge FDB for a MAC live | `watch -n 0.2 'bridge fdb show br vmbr0 \| grep <mac>'` |
| Capture both sides of a suspected hairpin drop | `tcpdump -i <tapA> -n host <dest>` / `tcpdump -i <tapB> -n host <src>` (run simultaneously) |
| Check VLAN-aware bridge port config | `bridge -d link show dev <tap>` |
| Check VLAN membership per tap | `bridge vlan show` |
| Check tc state on a tap | `tc qdisc show dev <tap>` / `tc filter show dev <tap>` |
| Check native nftables (post-PVE-9) | `nft list ruleset` |
| Check Proxmox per-VM firewall | `cat /etc/pve/firewall/<vmid>.fw` |
| Check bridge-nf-iptables interaction | `sysctl net.bridge.bridge-nf-call-iptables` |
| Force traffic off the local bridge, via router | `ip route add <dest>/32 via <gateway> dev <iface>` |
| Persist a host route (NetworkManager) | `nmcli connection modify "<conn>" +ipv4.routes "<dest>/32 <gateway>"` |

---

