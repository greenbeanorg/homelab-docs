# Pi-hole ↔ Kuma Auth Outage — Root Cause & Fix

Incident date: 2026-09-03 (first 401s) through 2026-09-07 (resolved).
Affects: `pihole` / `pihole2` monitors in Uptime Kuma.

---

## 1. Summary

Both Pi-hole monitors in Kuma went red with `Request failed with status
code 401` and never recovered. Root cause was a chain of three separate,
mostly self-inflicted issues layered on top of each other:

1. Pi-hole v6 requires a session (`SID`) for `/api/*` once a password is
   set. An earlier VLAN-migration session set the admin password on both
   instances (needed to get `pihole_vlan_sync.py`'s destructive PUT/DELETE
   calls working) — which silently broke the old **passwordless**
   `json-query` monitors that had been relying on Pi-hole having no
   password at all.
2. The replacement script (`picheck.sh`, a Push-monitor-based health
   check) never logged out of the sessions it created. Every cron tick
   left one behind, until Pi-hole's `webserver.api.max_sessions` cap (16)
   was exceeded and *new* logins started failing with `api_seats_exceeded`
   — indistinguishable from a real outage without checking the raw
   error body.
3. Separately, the two Push monitor tokens in `picheck.sh` were left as
   literal placeholders (`PRIMARY_PUSH_TOKEN` / `SECONDARY_PUSH_TOKEN`).
   The script only checks whether the *Pi-hole* side succeeded, not
   whether the push to Kuma landed — so it logged `pushed up` correctly
   and confidently for hours while actually 404ing against Kuma the
   whole time.

None of these were version regressions or bugs in Pi-hole/Kuma —
all three were consequences of decisions made earlier in the same
migration, surfacing one at a time.

---

## 2. Current state (what's actually running)

- **`pihole-push`** and **`pihole2-push`** — Push-type monitors in Kuma,
  fed by `picheck.sh`. These replaced the old `json-query` monitors
  (`pihole` / `pihole2`), which should be paused or deleted and removed
  from `inventory.yaml` — they can never recover now that a password is
  set on both instances.
- **`picheck.sh`** lives on `ellsworth`, runs via cron every 60s, logs
  to `~/picheck.log` (self-rotating, keeps last ~10k lines). Reads
  Pi-hole passwords from `~/.config/picheck.env` (`chmod 600`).
- **Uptime Kuma** itself was relocated from `~/uptime-kuma` to
  `/opt/uptime-kuma` on `ellsworth` during this incident, to match the
  convention used by `netbox`/`pulse`/`lan-dev-watcher`. Same Docker
  volume (`uptime-kuma_uptime-kuma-data`), no data migration needed —
  only the compose source path changed.
- **`webserver.api.max_sessions`** raised from 16 → 32 on both Pi-holes,
  as headroom against the leak class of bug even with the logout fix
  in place.
- **Pi-hole/DNS inventory management** (`kuma_sync.py`,
  `pihole_vlan_sync.py`, `inventory.yaml`) lives on `dority`, not
  `ellsworth` — a leftover duplicate copy under `/opt/uptime-kuma` on
  `ellsworth` was removed during this incident.

---

## 3. `picheck.sh` (current, working version)

```bash
#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="$HOME/.config/picheck.env"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

KUMA_BASE="http://localhost:3001"

log() { echo "$(date '+%F %T') $*"; }

push_result() {
  local URL="$1" LABEL="$2"
  local CODE
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "$URL")
  if [ "$CODE" != "200" ]; then
    log "WARNING: push to $LABEL failed, HTTP $CODE"
  fi
}

check_pihole() {
  local HOST="$1" PW="$2" TOKEN="$3"
  local SID=""

  if [ -z "$PW" ]; then
    push_result "$KUMA_BASE/api/push/$TOKEN?status=down&msg=pw_env_unset&ping=" "$HOST"
    log "[$HOST] password env var not set — pushed down"
    return
  fi

  SID=$(curl -s "http://$HOST/api/auth" -d "{\"password\":\"$PW\"}" | jq -r '.session.sid // empty')

  if [ -z "$SID" ]; then
    push_result "$KUMA_BASE/api/push/$TOKEN?status=down&msg=auth_failed&ping=" "$HOST"
    log "[$HOST] auth failed — pushed down"
    return
  fi

  local STATUS
  STATUS=$(curl -s -H "X-FTL-SID: $SID" "http://$HOST/api/dns/blocking" | jq -r '.blocking // empty')

  if [ "$STATUS" = "enabled" ]; then
    push_result "$KUMA_BASE/api/push/$TOKEN?status=up&msg=OK&ping=" "$HOST"
    log "[$HOST] blocking enabled — pushed up"
  else
    push_result "$KUMA_BASE/api/push/$TOKEN?status=down&msg=blocking_${STATUS:-unknown}&ping=" "$HOST"
    log "[$HOST] blocking status: ${STATUS:-unknown} — pushed down"
  fi

  # Always release the session — this is the fix for api_seats_exceeded
  curl -s -X DELETE -H "X-FTL-SID: $SID" "http://$HOST/api/auth" >/dev/null
}

check_pihole "10.79.10.250" "${PIHOLE_PW_10_79_10_250:-}" "<pihole-push-token>"
check_pihole "10.79.10.249" "${PIHOLE_PW_10_79_10_249:-}" "<pihole2-push-token>"

tail -n 10000 "$HOME/picheck.log" > "$HOME/picheck.log.tmp" && mv "$HOME/picheck.log.tmp" "$HOME/picheck.log"
```

`~/.config/picheck.env` (chmod 600):
```bash
export PIHOLE_PW_10_79_10_250="<the admin password>"
export PIHOLE_PW_10_79_10_249="<the same password>"
```

Crontab:
```
* * * * * /home/aba/picheck.sh >> /home/aba/picheck.log 2>&1
```

---

## 4. Quick reference — troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| `Request failed with status code 401` on a `json-query` monitor | Pi-hole has a password set; the monitor was written for the passwordless era | Confirm with `curl http://<host>/api/dns/blocking` — 401 with no auth header is expected once a password exists. Retire the monitor, use Push instead. |
| `picheck.sh` logs `auth failed`, error body says `api_seats_exceeded` | Session leak — script logging in without logging out, hit `max_sessions` | `pct exec <vmid> -- sed -n '/\[webserver.api\]/,/^\[/p' /etc/pihole/pihole.toml \| grep max_sessions`. Confirm the script calls `DELETE /api/auth` at the end of every run. |
| Kuma log shows clean `pushed up` every minute, but the monitor's own dashboard page shows Down | Push token in the script doesn't match the monitor's real token — script is 404ing against Kuma silently | `grep -n 'check_pihole "10' picheck.sh` — compare each token against the `Push:` URL shown on that monitor's own dashboard page. Add HTTP-status checking (`push_result` above) so this fails loudly next time instead of lying. |
| Kuma dashboard shows a stale status right after a container move/recreate | Old browser tab's Socket.IO connection dropped silently | Hard refresh (`Ctrl+Shift+R`) before assuming it's a real fault. |
| `docker compose down` runs but the container is still there / new one won't start (`name already in use`) | Container was never actually tied to that directory's Compose project (no labels) — `down` created a new network but touched nothing else | `docker inspect <name> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'` — empty means untracked. Use `docker compose ls -a` to find where a container's real config lives, and `docker stop && docker rm` directly rather than trusting `compose down` to find it. |

---

## 5. Follow-ups not yet done

- Confirm `pihole_vlan_sync.py` on `dority` also calls `DELETE /api/auth`
  after each run — it authenticates the same way and was a live suspect
  in the original seat exhaustion.
- `inventory.yaml` (both the `dority` copy and the stale `/opt` copy
  removed from `ellsworth`) is out of sync with several monitors' live,
  correct IPs (`OPNsense`, `MikroTik` — which also has a literal typo,
  `10.790.10.2` — `utter`, `swearengen`, `wu`) and still defines
  `pihole`/`pihole2` as `json-query` monitors that should be deleted
  from the file entirely now that they're Push-managed outside
  `kuma_sync.py`'s scope. Reconcile before the next sync run.
- Neither `~/uptime-kuma` nor `/opt/uptime-kuma` was ever a git
  repository — this whole toolkit (`compose.yaml`, sync scripts) isn't
  version-controlled on `ellsworth` yet, unlike the equivalent tooling
  on `dority`.
