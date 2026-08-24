# Uptime Kuma — Declarative Monitoring

Availability monitoring for the lab, running in Docker on the M4 Mac. Monitors
are **declared in `inventory.yaml`** and reconciled into Kuma by `kuma_sync.py`
over the Socket.IO API, so the monitor set is version-controlled rather than
click-configured.

- **Monitoring host:** `abas-M4` — macOS, Docker Desktop, static IP **10.x.x.235**
- **Container:** `uptime-kuma`, image `louislam/uptime-kuma:2`, host port **3001**
- **Repo path:** `~/homelab-monitoring/uptime-kuma`
- **Dashboard:** `http://10.x.x.235:3001/dashboard`
- **Date:** August 2026

---

## 1. Architecture

[#1-architecture](#1-architecture)

```
inventory.yaml  ──►  kuma_sync.py  ──►  Socket.IO API  ──►  Uptime Kuma (Docker)
 (desired state)      (reconciler)         :3001            (SQLite in volume)
```

`inventory.yaml` is the source of truth for **which** monitors exist and their
core settings — type, target, headers, accepted status codes, JSON query.
Everything else (check interval, retries, notifications, tags, status pages,
maintenance windows) is configured in the Kuma UI and is **not** managed by the
script.

Reconciliation is **name-keyed**: a Kuma monitor is matched to an inventory entry
by its `name`. Renaming in the YAML creates a second monitor rather than renaming
the existing one.

The script is deliberately **non-destructive** — it creates and updates, never
deletes. Monitors present in Kuma but absent from the inventory are reported as
`UNMANAGED` and left alone.

---

## 2. Files

[#2-files](#2-files)

| File | Purpose | Tracked |
| --- | --- | --- |
| `inventory.yaml` | Declared monitors, grouped site → category | yes |
| `kuma_sync.py` | Reconciler | yes |
| `requirements.txt` | Pinned Python dependencies | yes |
| `.env` | Kuma URL, credentials, `PLEX_TOKEN` | **no** |
| `.venv/` | Python virtualenv | no |

```gitignore
.env
.venv/
__pycache__/
*.pyc
*.bak
.DS_Store
```

**Secrets never appear in `inventory.yaml`.** They are referenced as `${VAR}` and
expanded from the environment at sync time, which is what makes the inventory
safe to commit.

### `.env` template

```dotenv
KUMA_URL=http://localhost:3001
KUMA_USERNAME=<kuma admin user>
KUMA_PASSWORD=<kuma admin password>
PLEX_TOKEN=<X-Plex-Token>
```

### Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> `python3 -m venv .venv` against an existing `.venv` is non-destructive — it
> refreshes `bin/` and `pyvenv.cfg` but leaves `site-packages` intact. Use
> `--clear` for an actual wipe.

---

## 3. `inventory.yaml` schema

[#3-inventoryyaml-schema](#3-inventoryyaml-schema)

Structure is `site: → category: → [monitors]`. Site and category group the sync
output only; they do not create Kuma tags or monitor groups.

Supported types (`TYPE_MAP` in `kuma_sync.py`): `ping`, `http`, `json-query`.
Anything else raises `ValueError` and aborts the entire run — the script is
fail-fast by design, so a typo can't silently skip a monitor.

```yaml
site:
  category:
    - name: Display name          # required — primary key, don't rename casually
      type: ping|http|json-query  # required
      target: <ip or url>         # required — hostname for ping, url otherwise
      headers: '{"K": "${VAR}"}'  # optional, http/json-query, JSON string
      accepted_statuscodes:       # optional, default ["200-299"]
        - "200"
      jsonPath: $.field           # json-query only
      jsonPathOperator: "=="      # json-query only, default "=="
      expectedValue: value        # json-query only
```

Current coverage — 12 monitors:

| Category | Monitors |
| --- | --- |
| network | OPNsense, MikroTik, AP |
| proxmox | `swearengen`, `wu` |
| storage | TrueNAS |
| services | Pi-hole, Pi-hole 2, Home Assistant, Nextcloud, SearXNG |
| media | Plex |

---

## 4. Operations

[#4-operations](#4-operations)

### Run a sync

```bash
cd ~/homelab-monitoring/uptime-kuma
source .venv/bin/activate
python kuma_sync.py --dry-run     # always first
python kuma_sync.py               # apply
```

Output legend:

| Line | Meaning |
| --- | --- |
| `CREATE` | In the inventory, not in Kuma |
| `UPDATE` | Exists, but type / target / headers / status codes / JSON query drifted |
| `OK` | No drift |
| `UNMANAGED` | In Kuma, not in the inventory — **never deleted** |

### Add a monitor

1. Add the entry to the appropriate site/category block in `inventory.yaml`.
2. `python kuma_sync.py --dry-run` — confirm exactly one `CREATE` and no
   unexpected `UPDATE` lines.
3. `python kuma_sync.py`.
4. Set interval, retries, and notifications in the UI (not script-managed).

### Retire a monitor

Delete it in the Kuma UI **and** remove it from `inventory.yaml`. Removing it
from only one place leaves either an unmanaged orphan or a monitor that gets
recreated on the next sync.

### Temporarily disable a host

Pause the monitor in the UI and leave the inventory entry in place. Pausing
doesn't change type/target/headers, so the next sync reports `OK` and the monitor
stays paused. For long-term removals, comment out the YAML block instead — see
the commented Jellyfin placeholder for the pattern.

---

## 5. Service-specific notes

[#5-service-specific-notes](#5-service-specific-notes)

### 5.1 Plex — `401 Request failed with status code 401`

[#51-plex--401](#51-plex--401)

Plex requires an auth token, passed as a header:

```yaml
headers: '{"X-Plex-Token": "${PLEX_TOKEN}"}'
accepted_statuscodes:
  - "200"
```

Token retrieval: Plex Web → any library item → **Get Info** → **View XML**. The
token is the `X-Plex-Token` query parameter on the resulting URL.

> **Gotcha:** `os.path.expandvars` leaves `${PLEX_TOKEN}` as a literal string when
> the variable is unset, so a missing `.env` entry sends a header containing the
> placeholder text and yields the same 401 — with no error from the script. See
> §8.5 for the guard.

Verify by hand:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-Plex-Token: $PLEX_TOKEN" http://10.x.x.110:32400/
```

### 5.2 Pi-hole — `403 Request failed with status code 403`

[#52-pi-hole--403](#52-pi-hole--403)

Pi-hole v6 replaced the legacy `admin/api.php` with a session-authenticated REST
API under `/api/*`. Endpoints return **403** without a valid session ID. Options,
in order of preference:

1. Configure an **app password** in Pi-hole and pass a session header, or
2. Point the monitor at an unauthenticated endpoint, or
3. Fall back to a plain `http` monitor against the admin page.

Both instances currently pass with a JSON query against the blocking status:

```yaml
type: json-query
target: http://10.x.x.250/api/dns/blocking
jsonPath: $.blocking
jsonPathOperator: "=="
expectedValue: enabled
```

Success message in Kuma: `JSON query passes (comparing enabled == enabled)`.
Inspect the raw response when it breaks:

```bash
curl -sS http://10.x.x.250/api/dns/blocking | jq .
```

### 5.3 AP — expected nightly downtime

[#53-ap--expected-nightly-downtime](#53-ap--expected-nightly-downtime)

**The AP is on a power timer.** It cuts around 22:00 and returns whenever the
first person is up. Overnight `Down` events on this monitor are expected and are
**not** a fault.

Handled with a Kuma **maintenance window** — the sync script has no concept of
maintenance, so this is UI state that must be recreated after a rebuild:

- Settings → Maintenance → Schedule Maintenance
- Strategy: **Recurring - Interval**, every 1 day
- Affected monitor: `AP`
- Window: **21:45 → 10:00**, deliberately wider than the timer since the morning
  power-on time varies
- Set the timezone explicitly rather than inheriting the server default
- If the cross-midnight window misbehaves, split into 21:45–23:59 and 00:00–10:00

Tradeoff: a genuine AP failure inside the window won't alert. Acceptable, since
the AP is powered off for most of it. Accumulated uptime percentage for this
monitor reflects scheduled downtime, not reliability — judge it on daytime
behaviour only.

Escalate only if the AP is down **outside** the window while other pings on the
same subnet stay healthy; that points at the AP itself or its PoE feed.

### 5.4 Timeouts — `timeout of 48000ms exceeded`

[#54-timeouts](#54-timeouts)

Kuma's HTTP request timeout, normally seen when a target VM is powered off. If a
**running** service reports this, check the VM before touching Kuma.

---

## 6. Notifications

[#6-notifications](#6-notifications)

Alerting is via **ntfy** push to phone. Configured in the Kuma UI, so it is UI-only
state that must be recreated after a rebuild (§7) — the sync script does not manage
notifications.

| Setting | Value |
| --- | --- |
| Provider | ntfy |
| Server | `https://ntfy.sh` (public instance) |
| Topic | see `.env` / password manager — **not recorded here** |
| Priority | 4 (high) — breaks through Do Not Disturb |
| Scope | Default enabled, applied to all existing monitors |

> **The topic name is a shared secret.** On the public ntfy.sh instance there are no
> accounts: anyone who knows the topic can read every alert and publish fake ones.
> It is a credential, not a config value, and does not belong in a public repo — hence
> the placeholder above. The pre-commit hook blocks `ntfy.sh/<topic>` URLs for this
> reason.

Phone-side subscription: install the ntfy app, subscribe to the topic, and leave both
**Use another server** (that is for self-hosted instances) and **Instant delivery in
doze mode** (persistent connection, costs battery) unchecked. Firebase delivery is
normally a few seconds.

### Verifying

The **Test** button in Kuma proves credentials only. To prove the whole path, stop
something real and watch for the push:

```bash
docker stop <non-critical-container>   # or pause/unpause a monitor in the UI
```

An untested alert path is indistinguishable from a working one right up until the
moment it matters.

### Ordering note

Configure the **AP maintenance window (§5.3) before** enabling notifications.
Otherwise the first night produces a 22:00 false positive, which is how people learn
to swipe alerts away without reading them.

### 6.1 Dead-man's switch (healthchecks.io)

ntfy alerts when a *monitored service* fails. Nothing in that path alerts when **Kuma
itself** stops — a dead Docker daemon, a wedged container, or a dropped uplink all
produce silence identical to everything being healthy. Silence is the one failure mode
a monitoring system cannot report on its own.

The switch inverts the direction: an **external** service expects a regular heartbeat
and alerts when the heartbeat stops.

| Setting | Value |
| --- | --- |
| Service | healthchecks.io (free tier — 20 checks) |
| Check name | `kuma-reachable` |
| Period / grace | 5 min / 10 min — alerts after ~15 min of silence |
| Heartbeat source | `pihole` (ODROID-XU4), root crontab |
| Integrations | ntfy (down: priority 5, up: 3) **and** email |

**Why the XU4 and not the Mac.** The heartbeat must originate somewhere that is *not*
the thing being monitored. The XU4 is the only host in the lab that depends on neither
Proxmox node — so it can still report that Kuma is unreachable when the reason Kuma is
unreachable is a hypervisor being down.

**The cron line:**

```bash
*/5 * * * * curl -fsS -m 8 -o /dev/null http://10.x.x.235:3001/ && curl -fsS -m 10 --retry 3 -o /dev/null https://hc-ping.com/<ping-url>
```

The `&&` is doing the real work — the heartbeat fires **only if Kuma actually
answered**. `-f` makes curl fail on an HTTP error rather than reporting a 502 as
success. Without the conditional, this would only detect the XU4 dying.

> **The ping URL is a credential.** Anyone holding it can send fake heartbeats and
> suppress alerts indefinitely — same category as the ntfy topic, and equally excluded
> from this repo.

⚠️ **Two failure modes that look like success:**

- **The check *name* is not the ping URL.** Pinging `https://hc-ping.com/<check-name>`
  returns HTTP 400. Either use the UUID from the check's page, or enable
  **Project Settings → Ping URL format → slugs**, which gives
  `https://hc-ping.com/<ping-key>/<slug>` and survives a rebuilt check. Slug format
  requires the check to already exist unless `?create=1` is appended.
- **Cron's `PATH` is minimal on Armbian.** If `curl` isn't found the line fails
  silently, which produces a *false alarm* rather than a missed one. Confirm with
  `which curl` and use an absolute path if it lives anywhere unusual.

**Verifying** — don't wait 15 minutes for a natural timeout:

```bash
curl -fsS https://hc-ping.com/<ping-url>/fail    # forces the check down, fires everything
```

Both the push and the email should land within seconds. Then let cron ping once and
confirm the check recovers to green.

Two delivery integrations is deliberate: the whole point is catching failures the
primary path can't report, and a single channel reintroduces exactly that dependency.

**Known gap:** this proves Kuma is *serving*, not that its checks are actually running.
Curling a status-page heartbeat endpoint instead of the root would exercise the real
monitoring pipeline.

---

## 7. Rebuild from scratch

[#7-rebuild-from-scratch](#7-rebuild-from-scratch)

```bash
# 1. Container
docker run -d --restart=unless-stopped \
  -p 3001:3001 \
  -v uptime-kuma:/app/data \
  --name uptime-kuma \
  louislam/uptime-kuma:2

# 2. First-run setup at http://localhost:3001 — create the admin user,
#    then put those credentials in .env

# 3. Sync tooling
cd ~/homelab-monitoring/uptime-kuma
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Populate
python kuma_sync.py --dry-run && python kuma_sync.py
```

Then recreate the UI-only state: **notifications** (§6), **check intervals**, and the
**AP maintenance window** (§5.3).

### Backup

All Kuma state — monitors, heartbeat history, notifications, users — lives in
SQLite inside the `uptime-kuma` Docker volume. `inventory.yaml` reproduces the
monitor definitions but **not** history, notification channels, or status pages.

```bash
docker run --rm \
  -v uptime-kuma:/data \
  -v "$PWD":/backup \
  alpine tar czf /backup/uptime-kuma-$(date +%F).tar.gz -C /data .
```

---

## 8. Known limitations of `kuma_sync.py`

[#8-known-limitations](#8-known-limitations)

Documented so they aren't rediscovered later.

1. **No deletes.** By design — unmanaged monitors are listed, not removed.
2. **No pause/resume management.** The script never sets `active`, so paused
   monitors stay paused. Worth verifying after an `edit_monitor`, since that call
   submits the full monitor object.
3. **Rename = duplicate.** `name` is the join key.
4. **Interval, retries, notifications, tags, status pages, and maintenance are
   UI-only.** They survive syncs but aren't captured by the YAML.
5. **Silent placeholder expansion.** Unset environment variables pass through
   literally. Suggested guard in `expand_env`:

   ```python
   import re

   def expand_env(value):
       if value is None:
           return None
       expanded = os.path.expandvars(value)
       missing = re.findall(r"\$\{(\w+)\}", expanded)
       if missing:
           raise ValueError(f"Unset environment variable(s): {', '.join(missing)}")
       return expanded
   ```

6. **Headers are never cleared.** Removing `headers` from a YAML entry reports the
   change but omits the kwarg, so the old header persists in Kuma. Pass
   `headers=""` explicitly to clear.
7. **Fixed working directory.** `INVENTORY_FILE = "inventory.yaml"` is relative;
   the script only works when run from its own directory. `load_dotenv()` has the
   same constraint, which is how a valid `PLEX_TOKEN` can appear to vanish.
8. **`:2` is a moving beta tag.** Pin a specific patch version once the 2.x line
   stabilises, so `docker pull` can't change behaviour underneath the script.

---

## 9. Structural caveats

[#9-structural-caveats](#9-structural-caveats)

- **The monitoring host sits inside the network it monitors.** If the LAN, the
  Mac, or Docker Desktop goes down, monitoring goes down with it — no longer
  *silently*, since the §6.1 heartbeat reports the silence from outside. A second
  Kuma instance on the off-site VPS would go further and keep monitoring running,
  not merely announce that it stopped.
- **macOS sleep stops monitoring.** Confirm the host never sleeps:

  ```bash
  pmset -g
  sudo pmset -a sleep 0 disablesleep 1
  ```

- **Docker Desktop must auto-start on login** for `--restart=unless-stopped` to
  survive a reboot.
- **Kuma is served over plain HTTP**, no TLS. Acceptable on a flat trusted LAN;
  revisit alongside the planned VLAN segmentation.
- **The dead-man's switch covers reachability, not correctness.** §6.1 alerts when Kuma
  stops answering, but a Kuma that serves happily while its own checks are stalled still
  looks healthy from outside.

---

## 10. Troubleshooting

[#10-troubleshooting](#10-troubleshooting)

| Symptom | Check |
| --- | --- |
| `ERROR: KUMA_USERNAME is not set in .env` | `.env` missing, or script run from the wrong directory |
| Login hangs at connect | Container up? `docker ps`. `curl -I http://localhost:3001` should return `302 → /dashboard` |
| Socket.IO handshake errors | Raise `SOCKET_SETTLE_DELAY` (2s) or `KUMA_TIMEOUT` (30s) |
| `Unsupported monitor type '<x>'` | Not in `TYPE_MAP` — only `ping`, `http`, `json-query` |
| Monitor shows 401 / 403 | Auth header — §5.1, §5.2 |
| Monitor shows timeout | Target powered off or unreachable; test with `curl` from the host |
| Ping down but host reachable from the Mac | Test inside the container: `docker exec uptime-kuma ping -c3 <ip>` |
| Duplicate monitors after an edit | A `name` changed — delete the orphan in the UI |
| Every run shows `UPDATE ... headers: [changed]` | Idempotency wart — compare what Kuma stores against what the script sends |

---

## Quick reference

[#quick-reference](#quick-reference)

| Item | Value |
| --- | --- |
| Monitoring host | `abas-M4` — 10.x.x.235, macOS + Docker Desktop |
| Container | `uptime-kuma`, `louislam/uptime-kuma:2`, port 3001 |
| Repo path | `~/homelab-monitoring/uptime-kuma` |
| Source of truth | `inventory.yaml` (12 monitors) |
| Reconciler | `kuma_sync.py`, `--dry-run` supported |
| Monitor types | `ping`, `http`, `json-query` |
| Secrets | `.env`, referenced as `${VAR}` in the inventory |
| Persistent state | Docker volume `uptime-kuma` (SQLite) |
| Deletes | Never — unmanaged monitors are reported only |
| Alerting | ntfy push, priority 4 — topic is a secret, kept out of this repo |
| Dead-man's switch | healthchecks.io `kuma-reachable`, 5/10 min, cron on the XU4 (§6.1) |
| UI-only state | Intervals, retries, notifications, tags, maintenance windows |
| Scheduled downtime | `AP` — nightly power timer, maintenance window 21:45–10:00 |
