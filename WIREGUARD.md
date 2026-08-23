# WireGuard — Multi-Site Overlay Network

Site-to-site and roaming VPN for the lab, built as **hub-and-spoke around a cloud
instance** so that neither residential endpoint needs to be reachable from the
internet. Every spoke dials out; nothing at either house requires a port forward,
a static WAN address, or dynamic DNS.

The overlay carries three networks: the home LAN, a remote site's LAN, and roaming
phones and laptops.

- **Hub:** `kk1` — Oracle Cloud Ampere A1, Ubuntu 24.04, UDP `51820`
- **Home spoke:** OPNsense VM on `wu` — subnet-routes `10.x.x.0/24`
- **Remote spoke:** `zombie` — Debian 13 VM — subnet-routes `192.168.x.0/24`
- **Roaming:** phones and laptops, full tunnel via the hub
- **Overlay:** `10.99.x.0/24`
- **Date:** August 2026

> **Masking note.** House style masks internal addresses as `10.x.x.N`. This
> document keeps the *second* octet on overlay addresses (`10.99.x.N`) so the
> overlay stays visually distinct from the home LAN — otherwise the router's LAN
> address and the hub's overlay address both render as `10.x.x.1` and the routing
> tables below become unreadable. RFC1918 space is not sensitive on its own; the
> masking is policy, and the policy is served either way.

---

## 1. Design

[#1-design](#1-design)

```
                     ┌─────────────────────────┐
                     │  kk1 (Oracle Cloud)     │
                     │  <HUB_PUBLIC_IP>:51820  │
                     │  wg0 = 10.99.x.1        │
                     │  hub / forwarder / NAT  │
                     └───┬──────────┬──────┬───┘
                         │          │      │
          ┌──────────────┘          │      └──────────────┐
          │                         │                     │
  ┌───────┴────────┐      ┌─────────┴────────┐   ┌────────┴────────┐
  │ OPNsense (home)│      │ zombie (remote)  │   │ phones / laptops│
  │ wg0=10.99.x.2  │      │ wg0=10.99.x.3    │   │ 10.99.x.11-.13  │
  │ LAN 10.x.x.0/24│      │ LAN 192.168.x/24 │   │ full tunnel     │
  └────────────────┘      └──────────────────┘   └─────────────────┘
```

### Why a cloud hub rather than direct site-to-site

A direct tunnel between the two houses requires one side to be reachable — a port
forward on a residential connection, plus dynamic DNS to survive WAN address
changes, plus cooperation from someone else's ISP router. The hub removes all of
that: **both** sides are dial-out only, and a changed WAN address on either end is
invisible to the design.

The costs are honest ones. All spoke-to-spoke traffic hairpins through the hub,
roughly doubling latency between the two sites, and the hub is a single point of
failure for the whole overlay. Both were acceptable here: the sites exchange
management traffic rather than bulk data, and the hub is a free-tier instance with
generous egress.

### Address plan

| Node | Overlay | Routes | Role |
| --- | --- | --- | --- |
| `kk1` | `10.99.x.1` | — | Hub, listens UDP `51820` |
| OPNsense | `10.99.x.2` | `10.x.x.0/24` | Spoke + subnet router |
| `zombie` | `10.99.x.3` | `192.168.x.0/24` | Spoke + subnet router (NAT) |
| phone1 | `10.99.x.11` | — | Full-tunnel roaming |
| phone2 | `10.99.x.12` | — | Full-tunnel roaming |
| laptop | `10.99.x.13` | — | Full-tunnel roaming |

**Check for overlap before building.** Oracle's default VCN CIDR is a `10.0/16`
supernet whose default subnet collides directly with a `10.x.x.0/24` home LAN. This
instance landed on a different third octet and needed no renumbering, but the check
is not optional — verify with `ip route` on the instance and confirm no supernet
route competes with the tunnel routes.

---

## 2. Authentication model

[#2-authentication-model](#2-authentication-model)

WireGuard has no user authentication. No certificates, no PKI, no passwords, no
enrollment. Each peer holds a Curve25519 keypair, and a peer is authorized **iff**
its public key appears in the other side's configuration. Packets from unknown keys
are dropped without a reply, which is why the listener does not respond to scanners
at all.

Each peer additionally carries a 32-byte preshared key mixed into the handshake as
a symmetric layer. It costs nothing and is applied on every peer here.

`AllowedIPs` does double duty and deserves specific attention:

- **As an ACL** it defines what source addresses a peer is permitted to send from.
- **As a routing table** it defines what destinations get sent down that tunnel.

One field, two jobs. Most misconfigurations in this build traced back to it, and
the symptoms rarely pointed at routing.

**Adding a subnet requires edits in more than one place.** A new network behind a
spoke must be added to that peer's block on the hub *and* to the `AllowedIPs` of
every other spoke that should reach it. Miss one and traffic flows in exactly one
direction, which reads like a firewall problem.

If key management ever outgrows hand-edited config files, the migration path is
Headscale on the hub — same data plane, adding enrollment, rotation, and ACLs.

---

## 3. Hub — `kk1`

[#3-hub--kk1](#3-hub--kk1)

### 3.1 Cloud-side prerequisites

Convert the instance's public IP from **ephemeral** to **reserved** before
distributing any configs. An ephemeral address changes on stop/start and breaks the
`Endpoint` on every peer simultaneously.

Open the listener in the VCN Security List (or the NSG attached to the VNIC):

| Field | Value |
| --- | --- |
| Stateless | No |
| Source | `0.0.0.0/0` |
| Protocol | UDP |
| Destination port | `51820` |

**No host firewall rule was required on this image.** Ubuntu 24.04 cloud images
ship `nft` with empty chains at `policy accept` and `ufw` inactive. Verify rather
than assume — older images ship a populated ruleset that rejects everything except
SSH, and a missing host rule produces total silence with no log entry.

```bash
nft list ruleset
```

> **Never flush the ruleset on a cloud instance.** The link-local `169.254/16` route
> carries iSCSI traffic to the boot volume. Flushing has hung instances mid-session
> with no recovery short of a console reset. Add and delete specific rules only.

### 3.2 Interface

`/etc/wireguard/wg0.conf`:

```ini
[Interface]
Address    = 10.99.x.1/24
ListenPort = 51820
PrivateKey = <HUB_PRIVATE_KEY>
MTU        = 1420

PostUp   = nft add table ip wgnat
PostUp   = nft add chain ip wgnat postrouting { type nat hook postrouting priority 100 \; }
PostUp   = nft add rule ip wgnat postrouting oifname "enp0s6" ip saddr 10.99.x.0/24 masquerade
PostDown = nft delete table ip wgnat

# --- Home / OPNsense ---
[Peer]
PublicKey    = <HOME_PUBLIC_KEY>
PresharedKey = <HOME_PSK>
AllowedIPs   = 10.99.x.2/32, 10.x.x.0/24

# --- Remote site / zombie ---
[Peer]
PublicKey    = <REMOTE_PUBLIC_KEY>
PresharedKey = <REMOTE_PSK>
AllowedIPs   = 10.99.x.3/32, 192.168.x.0/24

# --- Roaming clients (one block each) ---
[Peer]
PublicKey    = <CLIENT_PUBLIC_KEY>
PresharedKey = <CLIENT_PSK>
AllowedIPs   = 10.99.x.11/32
```

> **The explicit `MTU` line is mandatory here.** The instance NIC runs at 9000 —
> cloud VCNs use jumbo frames internally. Without an explicit value, `wg-quick`
> derives MTU from the default route's interface (9000 − 80 = **8920**) and builds
> packets that black-hole on any real internet path. Verify after every start:
> `ip -br link show wg0` must read 1420.

The masquerade rule exists **only** so full-tunnel roaming clients can reach the
internet through the hub. Spoke-to-spoke forwarding needs no rule, because the
forward chain policy is already `accept`.

Forwarding must be enabled and persistent:

```bash
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-wg.conf
sysctl --system
```

The nft table is rebuilt by `wg-quick` on every start, so no separate rule
persistence package is needed.

---

## 4. Home spoke — OPNsense

[#4-home-spoke--opnsense](#4-home-spoke--opnsense)

WireGuard is in-kernel and built into OPNsense 26.7. No plugin installation.

**VPN → WireGuard → Peers**

| Field | Value |
| --- | --- |
| Name | `oci-hub` |
| Public key | `<HUB_PUBLIC_KEY>` |
| Pre-shared key | `<HOME_PSK>` |
| Allowed IPs | `10.99.x.0/24, 192.168.x.0/24` |
| Endpoint address | `<HUB_PUBLIC_IP>` |
| Endpoint port | `51820` |
| Keepalive | `25` |

The field is labelled **Pre-shared key** in 26.7; older documentation calls it
"Shared Secret". Including `192.168.x.0/24` here is what gives the home LAN a route
toward the remote site — omit it and packets for that subnet never enter the tunnel.

**VPN → WireGuard → Instances**

| Field | Value |
| --- | --- |
| Name | `wg-hub` |
| Private key | `<HOME_PRIVATE_KEY>` |
| Public key | auto-fills — confirm it matches the hub's peer entry |
| Listen port | *(blank — dial-out only)* |
| Tunnel address | `10.99.x.2/32` |
| MTU | `1420` |
| Peers | `oci-hub` |
| Disable routes | unchecked |

The public key auto-filling to the expected value is a useful confirmation that the
correct private key was pasted.

**Firewall → Rules → WireGuard group** — traffic arriving from the tunnel is
filtered like any other interface, and nothing passes by default:

| Field | Value |
| --- | --- |
| Action | Pass |
| Direction | in |
| Protocol | any |
| Source | `10.99.x.0/24` |
| Destination | specific hosts and services |

No WAN rule is needed: the hub never initiates.

Routes are installed automatically from the peer's `AllowedIPs` and can be confirmed
with `netstat -rn | grep 192.168`. Traffic into the LAN is **routed, not NATed** —
replies show a single TTL decrement, so LAN hosts see genuine overlay source
addresses rather than the router's.

---

## 5. Remote spoke — `zombie`

[#5-remote-spoke--zombie](#5-remote-spoke--zombie)

A minimal Debian 13 VM on the remote Proxmox host, bridged onto that LAN with a
static address.

> **Use a VM, not an LXC.** `wg-quick` needs `CAP_NET_ADMIN` in the host network
> namespace to create the interface, which unprivileged containers do not have.
> Workarounds exist — privileged containers, moving the netdev in from the host —
> but they break across kernel upgrades on a machine that is not physically
> accessible.

```ini
[Interface]
Address    = 10.99.x.3/32
PrivateKey = <REMOTE_PRIVATE_KEY>
MTU        = 1420

PostUp   = nft add table ip wgnat
PostUp   = nft add chain ip wgnat postrouting { type nat hook postrouting priority 100 \; }
PostUp   = nft add rule ip wgnat postrouting iifname "wg0" oifname "ens18" masquerade
PostDown = nft delete table ip wgnat

[Peer]
PublicKey    = <HUB_PUBLIC_KEY>
PresharedKey = <REMOTE_PSK>
Endpoint     = <HUB_PUBLIC_IP>:51820
AllowedIPs   = 10.99.x.0/24, 10.x.x.0/24
PersistentKeepalive = 25
```

`PersistentKeepalive` is load-bearing on this node specifically. It sits behind a
residential NAT with no inbound rule; without periodic traffic the NAT mapping
expires and the tunnel silently becomes one-way until something originates locally.

### 5.1 Why this node NATs, and what that costs

The remote router is not under our administrative control, so it has no route back
toward the overlay or the home LAN. `zombie` therefore masquerades tunnel traffic
onto the remote LAN, and remote hosts see connections sourced from `zombie` itself.

**Consequence:** the home site can reach the remote LAN; the remote LAN cannot
initiate connections back. For a network belonging to someone else this is the
correct default — it requires zero configuration on their equipment and cannot
surprise them.

To make it symmetric later: remove the three `PostUp` lines and add a static route
on the remote router pointing `10.99.x.0/24` and `10.x.x.0/24` at `zombie`.

---

## 6. Roaming clients

[#6-roaming-clients](#6-roaming-clients)

The official WireGuard app on iOS and Android. Configs are generated on the hub and
transferred as QR codes, so no key material passes through a messaging app:

```bash
qrencode -t ansiutf8 < /tmp/client.conf && shred -u /tmp/client.conf
```

```ini
[Interface]
PrivateKey = <CLIENT_PRIVATE_KEY>
Address    = 10.99.x.11/32
DNS        = 10.x.x.250, 10.x.x.249
MTU        = 1420

[Peer]
PublicKey    = <HUB_PUBLIC_KEY>
PresharedKey = <CLIENT_PSK>
Endpoint     = <HUB_PUBLIC_IP>:51820
AllowedIPs   = 0.0.0.0/0
PersistentKeepalive = 25
```

Full tunnel is the default here, which puts Pi-hole filtering on cellular and makes
untrusted wifi safe to use. **No `::/0`** — the hub has no IPv6 address, and
advertising a v6 default route without one creates a black hole. Full tunnel still
works on IPv6-only mobile networks because the handset's CLAT reaches the v4
endpoint.

For split tunnel instead, list the three internal subnets in `AllowedIPs` and
**remove the `DNS` line** — otherwise name resolution breaks whenever the tunnel is
down.

Delete each client private key from the hub once it has been transferred.

### 6.1 Tethering does not work — by design

Phone hotspot traffic **bypasses the VPN on both platforms**. iOS routes Personal
Hotspot below the layer where the tunnel provider sits; Android's `VpnService`
captures application traffic only, and tethering happens beneath it. No setting
changes this, and neither UI indicates it is happening — the phone is protected and
everything behind it is not.

The working approach for tunneling multiple devices away from home is a travel
router (GL.iNet or similar OpenWrt hardware) holding its own peer config and
broadcasting an SSID, backhauling over hotel wifi, ethernet, or a tethered phone.

---

## 7. Verification

[#7-verification](#7-verification)

Bring-up order is hub, then spokes, then clients.

```bash
# On the hub
wg show                                   # every peer, handshake under 2 minutes
ip route | grep -E '10\.|192\.168'        # one route per subnet-routing peer
```

Reachability, walking outward:

| From | Target | Proves |
| --- | --- | --- |
| hub | spoke overlay address | tunnel carries traffic |
| hub | LAN address behind spoke | spoke forwarding and routing |
| spoke | LAN address behind *other* spoke | hub forwarding, spoke-to-spoke |
| LAN host | LAN address behind other site | end-to-end, including local firewall |

**Test from a LAN host, not from the router.** Pings originated on OPNsense source
from WAN by default and will not match the expected return path — use a LAN client
or specify the source address explicitly.

**TTL identifies the path.** `64` is a direct peer, `63` one router hop, `62` two
hops — spoke to hub to spoke. This is faster than reading routing tables when
diagnosing an asymmetric path.

### 7.1 MTU verification

Do not skip this. From a LAN host:

```bash
ping -M do -s 1372 <hub-overlay>     # 1400 total — must pass
ping -M do -s 1392 <hub-overlay>     # 1420 total — must pass
ping -M do -s 1472 <hub-overlay>     # 1500 total — must fail
```

Pass, pass, fail is correct. If the second fails, drop MTU to 1380 on every node.
The failure mode for an unverified MTU is distinctive and misleading: ping and SSH
work perfectly while HTTPS and NFS hang indefinitely.

### 7.2 Observed baselines

| Path | RTT |
| --- | --- |
| hub → home | ~32 ms |
| hub → remote site | ~39 ms |
| remote site → home LAN (via hub) | ~67 ms |

The spoke-to-spoke figure is roughly the sum of the two spoke legs, as expected for
a hairpin.

---

## 8. Diagnostic signatures

[#8-diagnostic-signatures](#8-diagnostic-signatures)

Three failure modes encountered during this build, recorded because the symptoms
pointed somewhere other than the cause.

### 8.1 Debugging technique

Bring the interface up in the foreground rather than through systemd. Each step
echoes with a `[#]` prefix and the failing command is the last one printed;
`systemctl status` shows only an exit code. Note that `wg-quick` deletes the
interface on any failure, so a `Device "wg0" does not exist` message is a
consequence of an earlier error, not the error itself.

A failed `systemctl reload` leaves the running interface untouched — config errors
cannot drop live tunnels.

### 8.2 Preshared key mismatch — the 148/92 signature

`tcpdump -ni <wan-nic> udp port 51820` on the hub showed a clean request/response
pair repeating roughly every five seconds: **148 bytes inbound** (handshake
initiation), **92 bytes outbound** (handshake response), and no session ever
established.

The hub *answering* proves both public keys are correct — it decrypted the
initiation and matched the peer. But in the Noise IK handshake the preshared key is
not mixed in until the response is constructed, so the responder replies regardless
of PSK and only the initiator discovers the mismatch. Initiation in, response out,
nothing completes, retry forever.

**148 in / 92 out on repeat means PSK mismatch.** Silence means firewall or routing.

Related: the `endpoint` field appears on a peer as soon as *any* packet arrives from
it, including one that fails authentication. No endpoint line at all means nothing
is arriving — a reachability problem, not a key problem.

### 8.3 Handshake established, ping dead

Tunnel up, handshake recent, transfer counters moving, and 100% packet loss. Echo
requests were arriving and being dropped by the OPNsense filter before anything
could answer. The signature is a **healthy handshake with asymmetric byte counters**
— more received than sent — which means packets are arriving and being discarded
locally rather than lost in transit.

### 8.4 The masquerade source-match bug

The remote spoke's NAT rule originally matched on source subnet:

```
oifname "ens18" ip saddr 10.99.x.0/24 masquerade
```

Traffic from the home LAN arrives at that node sourced from `10.x.x.N` — **not** an
overlay address. It did not match, so it was not translated; the node forwarded it
onto the remote LAN with its original source, and the remote router had no route
back and dropped the reply.

**Why it was hard to see:** the hub could reach the remote LAN perfectly, because
the hub sources from an overlay address that *did* match. Every tunnel was healthy,
every route was present, forwarding was enabled, and exactly one direction was
silently broken.

The fix is to match the inbound interface rather than a source subnet:

```
iifname "wg0" oifname "ens18" masquerade
```

`iifname "wg0"` covers everything arriving through the tunnel — home LAN, overlay,
and roaming clients — and requires no maintenance when a subnet is added.

---

## 9. Known limitations

[#9-known-limitations](#9-known-limitations)

- **The hub is a single point of failure.** Every path between sites runs through
  one free-tier instance. Losing it takes down the entire overlay, including
  spoke-to-spoke traffic that has no logical need for it. A second hub with a
  second peer block on each spoke would fix this and has not been built.
- **The hub sees all inter-site traffic in plaintext.** Traffic is decrypted and
  re-encrypted at the hub rather than passing through end-to-end. This is inherent
  to hub-and-spoke routing, not a misconfiguration, and it means the hub's own
  security posture bounds the security of the whole overlay.
- **The remote site cannot initiate.** A consequence of the NAT approach in §5.1,
  chosen deliberately, but it means the remote Pi-hole and game server cannot be
  configured to reach anything at home.
- **Free-tier instances can be reclaimed when idle.** A WireGuard hub consumes
  almost no CPU. The instance carries additional workload for this reason, and the
  policy is worth re-checking periodically.
- **Client key management is manual.** Adding a device means editing a config file
  and reloading. This is fine at six peers and will not be fine at twenty;
  Headscale is the documented migration path.
- **`AllowedIPs` maintenance is distributed.** There is no single place that
  defines "which networks exist" — adding one means editing the hub and every spoke
  that should reach it. This is the most likely source of future misconfiguration.

---

## 10. Rebuild from nothing

[#10-rebuild-from-nothing](#10-rebuild-from-nothing)

1. Provision the cloud instance; reserve its public IP; open UDP `51820` in the
   Security List. Verify no address overlap with either LAN.
2. Generate all keypairs and preshared keys centrally, one pair per node.
3. Write the hub config with every peer block; enable and start; **verify MTU
   reads 1420**.
4. Configure each spoke, verifying handshakes one at a time rather than in
   parallel — a single failing spoke is far easier to diagnose alone.
5. Add firewall rules on OPNsense; confirm reachability walking outward per §7.
6. Run the MTU test from a LAN host.
7. Distribute client configs by QR code; delete client private keys from the hub.
8. Restore monitoring: overlay ping monitors on all three node addresses.

Recovery of key material comes from the restic backup of `/etc/wireguard` on the
hub and remote node. Losing the hub's configuration without a backup means
re-keying every peer by hand.

---

## Quick reference

| | |
| --- | --- |
| Hub | `kk1` — Oracle Cloud, `<HUB_PUBLIC_IP>:51820`, overlay `10.99.x.1` |
| Home spoke | OPNsense VM on `wu` — overlay `10.99.x.2`, routes `10.x.x.0/24` |
| Remote spoke | `zombie` — Debian 13 VM — overlay `10.99.x.3`, routes `192.168.x.0/24` |
| Config path | `/etc/wireguard/wg0.conf` (mode 600) on hub and remote |
| Service | `systemctl {status,restart,reload} wg-quick@wg0` |
| Status | `wg show` — endpoint, handshake age, transfer counters |
| Handshake ages only | `wg show wg0 latest-handshakes` |
| Add a peer | Append `[Peer]` block, then `systemctl reload` — live tunnels survive |
| Revoke a peer | Delete the block, reload — key is dead immediately |
| Verify MTU | `ip -br link show wg0` must read 1420, never 8920 |
| Confirm NAT rule | `nft list table ip wgnat` |
| Watch handshakes | `tcpdump -ni <nic> udp port 51820` — 148 in / 92 out = PSK mismatch |
| OPNsense UI | VPN → WireGuard → Instances / Peers / Status |
| OPNsense routes | `netstat -rn | grep 192.168` |
| Monitoring | Uptime Kuma ping monitors on all three overlay addresses |
