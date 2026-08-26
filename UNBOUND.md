# UNBOUND — Recursive Resolution for Both Pi-holes

Local recursive, validating resolvers on both DNS hosts, replacing Quad9 as upstream.
Each Pi-hole gets its **own** Unbound — deliberately not a shared instance — so the
resolvers stay in separate failure domains exactly as [DNS.md §1](DNS.md#1-design)
argues they should. A shared Unbound would put one dependency underneath two things
designed not to share one.

- **Primary:** Unbound on the ODROID-XU4 host, `127.0.0.1:5335` — reached from the
  Pi-hole container via `network_mode: host`, no plumbing required
- **Secondary:** Unbound inside LXC 1000 on `wu`, same port
- **Install method:** `apt`, both sides — narrows the install-method asymmetry flagged
  in [DNS.md §7](DNS.md#7-known-limitations) rather than adding a third method
- **Date:** August 2026

| Question | Document |
| --- | --- |
| Why two resolvers, DHCP option 6 | [DNS.md §1–2](DNS.md) |
| Primary container build | [PIHOLE-DOCKER.md](PIHOLE-DOCKER.md) |
| Getting Armbian onto the XU4 | [ODROID-XU4.md](ODROID-XU4.md) |
| **Recursive resolution underneath both** | **this document** |

---

## 1. Why recursive instead of Quad9

Today's upstream (`9.9.9.9;149.112.112.112`) is plain UDP 53 to a third party. Two
things change with Unbound in front:

- **No third party sees every query.** The ISP still does — recursion resolves
  authoritative-server-by-authoritative-server over the same unencrypted path, so this
  is a better *distribution* of trust, not anonymity. If DoT/DoH to Quad9 was ever the
  goal instead, recursion is the wrong direction to move.
- **DNSSEC validation is performed, not reported.** Quad9 tells you a query validated;
  Unbound proves it locally against the root trust anchor.

**What's given up:** Quad9's malware/threat-intel blocking. Offset by stepping HaGeZi
TIF from medium to full in gravity — see [DNS.md §5](DNS.md#5-keeping-the-two-in-parity).
Not a like-for-like swap: TIF is reputation-based like Quad9 was, but a different feed
with different latency to brand-new threats.

**What changes in failure mode.** Currently: Pi-hole up → DNS works. After: Pi-hole
can be green while Unbound's trust anchor is stale or `root.hints` is broken, and
nothing resolves. `dns-root-data` + `unbound-anchor` (installed below) is what keeps
that from being a standing risk rather than a one-time setup step.

---

## 2. Install

Same package set, same config file, on both hosts — only the shell context differs.

**Primary — on the XU4 host itself (not the container):**

```bash
ssh pihole
sudo apt update && sudo apt install -y unbound unbound-anchor dns-root-data
```

**Secondary — inside LXC 1000 on `wu`:**

```bash
ssh wu
pct enter 1000
apt update && apt install -y unbound unbound-anchor dns-root-data
```

`dns-root-data` keeps `root.hints` current via package updates. `unbound-anchor`
bootstraps and maintains the DNSSEC trust anchor automatically — the piece that
prevents "validates today, silently stops validating in six months."

---

## 3. Config

Identical on both hosts:

```bash
tee /etc/unbound/unbound.conf.d/pihole.conf << 'EOF'
server:
    interface: 127.0.0.1
    port: 5335
    do-ip4: yes
    do-ip6: no
    do-udp: yes
    do-tcp: yes

    # Recursion only from Pi-hole on this box
    access-control: 127.0.0.0/8 allow
    access-control: 0.0.0.0/0 refuse

    root-hints: "/usr/share/dns/root.hints"
    harden-glue: yes
    harden-dnssec-stripped: yes
    use-caps-for-id: no
    edns-buffer-size: 1232
    prefetch: yes
    prefetch-key: yes
    cache-min-ttl: 300
    cache-max-ttl: 86400
    num-threads: 1
    msg-cache-size: 8m
    rrset-cache-size: 8m
    key-cache-size: 4m
    neg-cache-size: 1m
    outgoing-range: 60
    so-rcvbuf: 1m
EOF

systemctl enable --now unbound
```

Sized deliberately small — `num-threads: 1`, single-digit-MB caches. The XU4 is a
quad-core ARMv7 board that's also running the Pi-hole container; the LXC on `wu` has
headroom to spare, but there's no downside to the same small config there either, so
it stays identical rather than diverging per host for no functional reason.

> **Observed quirk — `pihole2` only.** First start logs:
> `subnetcache: prefetch is set but not working for data originating from the subnet
> module cache`. That LXC's Unbound package ships with the ECS subnet module compiled
> in and auto-loaded, even though nothing in this config enables it. Harmless — no
> client-subnet data is ever sent — but it's a real difference between the two hosts'
> package builds, not a misconfiguration. Don't "fix" it by chasing subnet-module
> settings that were never set.

---

## 4. Validation — before touching Pi-hole's upstream

Three checks, run against Unbound directly on **each** host, independently of Pi-hole:

```bash
dig @127.0.0.1 -p 5335 example.com                     # resolves at all
dig @127.0.0.1 -p 5335 +dnssec cloudflare.com | grep -i "flags\|RRSIG"   # ad flag + RRSIG present
dig @127.0.0.1 -p 5335 dnssec-failed.org                # expect SERVFAIL, no ad flag
```

⚠️ **A timeout is not a `SERVFAIL`, and the difference matters.** The first validation
attempt here used `sigfail.verteiltesysteme.net`, an old academic DNSSEC test domain,
and it timed out rather than returning `SERVFAIL` — indistinguishable at a glance from
validation working, but actually just a dead test domain. `dnssec-failed.org`
(Comcast/Verisign-maintained) gave a clean, immediate `SERVFAIL` with no `ad` flag.
Confirm you have an actual rejection, not a domain that stopped responding.

Both hosts must pass all three before moving to §5. This is the point where a mistake
costs nothing — nothing on the network is depending on Unbound yet.

---

## 5. Cutover — flip Pi-hole's upstream

⚠️ **Not a fallback pair.** Setting Pi-hole's upstream to
`127.0.0.1#5335;9.9.9.9` does not make Quad9 a backup. Pi-hole's dnsmasq layer treats
multiple upstreams as a set to distribute load across, not a priority list — a real
share of queries would go to Quad9 regardless of Unbound's health, quietly defeating
the point of doing this at all. It's Unbound alone or not at all.

**Primary first**, because option 6 is not failover ([DNS.md §2](DNS.md#2-dhcp-handoff-kea-dhcpv4))
— clients that happened to pick the primary get validated silently before anything
network-wide is at risk. Doing the secondary first risks real breakage for clients that
landed there.

In `/opt/pihole/docker-compose.yml` (or `.env`, per current layout):

```
FTLCONF_dns_upstreams: '127.0.0.1#5335'
```

```bash
cd /opt/pihole && docker compose up -d
dig +short @10.x.x.250 example.com
dig +short @10.x.x.250 dnssec-failed.org      # expect empty / SERVFAIL through Pi-hole too
```

Let it run under real client load for a while before touching the secondary. Then, on
`pihole2`, the equivalent config change and the same two `dig` checks against
`10.x.x.249`.

---

## 6. Rollback

Per host, independently — this is the payoff of two separate Unbound instances rather
than one shared point of failure:

```
FTLCONF_dns_upstreams: '9.9.9.9;149.112.112.112'
```

then `docker compose up -d` (primary) or the equivalent Pi-hole restart (secondary).
No Unbound teardown needed — reverting the upstream is sufficient, and Unbound can keep
running idle while the cause is investigated.

---

## 7. Ongoing verification

```bash
systemctl status unbound
dig @127.0.0.1 -p 5335 dnssec-failed.org        # re-run periodically — see §1 on silent failure
unbound-anchor -v                               # confirm trust anchor is current, both hosts
```

An Unbound that stopped validating looks identical to one that's fine, right up until
it matters — same shape as the untested-alert-path problem in
[UPTIME-KUMA.md §6](UPTIME-KUMA.md#6-notifications). Don't assume; re-check the
`dnssec-failed.org` result occasionally rather than only at install time.

---

## 8. Known limitations

- **Cold-cache queries are slower.** First-visit domains go from ~20ms anycast to a few
  hundred ms of full recursion. `prefetch: yes` and a warm cache close most of this gap
  for repeat traffic; it doesn't help the first hit.
- **Quad9's threat-intel blocking is gone**, offset by stepping HaGeZi TIF to full in
  gravity — see [DNS.md §5](DNS.md#5-keeping-the-two-in-parity). A newly-registered-
  domains list would close more of the gap but was deliberately left out: it flags
  freshly registered domains on sight, a poor fit for a household that registers its
  own domains regularly.
- **New single point of failure per host.** A stale trust anchor or corrupt
  `root.hints` on either box takes down resolution for that resolver while Pi-hole
  itself reports healthy. `dns-root-data`/`unbound-anchor` (§2) is the mitigation, not
  a guarantee.
- **The two hosts' Unbound builds are not identical** — see the subnetcache quirk in
  §3. Config is identical; packaging isn't. Don't assume a fix on one host applies
  cleanly to the other without checking.

---

## Quick reference

| Item | Value |
| --- | --- |
| Config file (both hosts) | `/etc/unbound/unbound.conf.d/pihole.conf` |
| Listen address | `127.0.0.1:5335` |
| Test resolution | `dig @127.0.0.1 -p 5335 example.com` |
| Test DNSSEC pass | `dig @127.0.0.1 -p 5335 +dnssec cloudflare.com` → `ad` flag + `RRSIG` |
| Test DNSSEC fail | `dig @127.0.0.1 -p 5335 dnssec-failed.org` → `SERVFAIL`, no `ad` flag |
| Trust anchor status | `unbound-anchor -v` |
| Restart | `systemctl restart unbound` |
| Rollback | Revert `FTLCONF_dns_upstreams` to `9.9.9.9;149.112.112.112`, restart Pi-hole |
