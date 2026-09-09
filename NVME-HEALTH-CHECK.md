# NVMe Health Check

Quick SMART health report for the 2TB NVMe (OS + VM storage) drive on each
Proxmox host — `swearengen`, `wu`, and the remote Proxmox host. Uses
`smartctl` from `smartmontools` to pull real drive telemetry instead of
guessing from `dmesg`.

## Requirements

Install on each host:

```bash
apt install smartmontools
```

## What it checks

Per detected `/dev/nvme*n1` device:

- Critical Warning bit
- Percentage Used (wear)
- Available Spare vs. its threshold
- Media and Data Integrity Errors
- Temperature, Power On Hours, Power Cycles, Unsafe Shutdowns

Flags **WARNING** if wear ≥80% or spare ≤20%; flags **CRITICAL** if the
drive's own critical-warning bit is set or it has logged media errors.
Exits 0/1/2 accordingly, so it can be wired into cron + a notification
later.

## nvme-health-check.sh

Run locally (as root) on each Proxmox host.

```bash
#!/usr/bin/env bash
# nvme-health-check.sh — quick SMART health report for NVMe drives
# Run locally on each Proxmox host (swearengen / wu / remote host).
# Requires smartmontools: apt install smartmontools
set -euo pipefail

if ! command -v smartctl >/dev/null 2>&1; then
    echo "smartctl not found. Install with: apt install smartmontools" >&2
    exit 1
fi

WARN_PCT_USED=80     # flag if wear (Percentage Used) hits this %
WARN_SPARE_PCT=20    # flag if Available Spare drops to/below this %

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

host="$(hostname)"
echo "=== NVMe Health Report: ${host} — $(date '+%Y-%m-%d %H:%M:%S') ==="
echo

devices=$(ls /dev/nvme[0-9]n1 2>/dev/null || true)
if [ -z "$devices" ]; then
    echo "No NVMe devices found (/dev/nvme*n1)."
    exit 0
fi

overall_status=0

for dev in $devices; do
    echo "--- $dev ---"
    out=$(smartctl -a "$dev" 2>/dev/null || true)

    model=$(echo "$out"     | grep -i "Model Number"                 | awk -F: '{print $2}' | xargs)
    serial=$(echo "$out"    | grep -i "Serial Number"                | awk -F: '{print $2}' | xargs)
    crit_warn=$(echo "$out" | grep -i "Critical Warning"             | awk -F: '{print $2}' | xargs)
    temp=$(echo "$out"      | grep -i "^Temperature:"                | head -1 | awk -F: '{print $2}' | xargs)
    spare=$(echo "$out"     | grep -i "Available Spare:"             | head -1 | awk -F: '{print $2}' | tr -d ' %')
    spare_thr=$(echo "$out" | grep -i "Available Spare Threshold"    | awk -F: '{print $2}' | tr -d ' %')
    pct_used=$(echo "$out"  | grep -i "Percentage Used:"             | awk -F: '{print $2}' | tr -d ' %')
    media_err=$(echo "$out" | grep -i "Media and Data Integrity Errors:" | awk -F: '{print $2}' | xargs)
    pwr_hrs=$(echo "$out"   | grep -i "Power On Hours:"              | awk -F: '{print $2}' | xargs)
    pwr_cyc=$(echo "$out"   | grep -i "Power Cycles:"                | awk -F: '{print $2}' | xargs)
    unsafe=$(echo "$out"    | grep -i "Unsafe Shutdowns:"            | awk -F: '{print $2}' | xargs)

    echo "Model:            ${model:-n/a}"
    echo "Serial:           ${serial:-n/a}"
    echo "Temperature:      ${temp:-n/a}"
    echo "Power On Hours:   ${pwr_hrs:-n/a}"
    echo "Power Cycles:     ${pwr_cyc:-n/a}"
    echo "Unsafe Shutdowns: ${unsafe:-n/a}"
    echo "Available Spare:  ${spare:-n/a}% (threshold ${spare_thr:-n/a}%)"
    echo "Percentage Used:  ${pct_used:-n/a}%"
    echo "Media Errors:     ${media_err:-n/a}"
    echo "Critical Warning: ${crit_warn:-n/a}"

    status="OK"
    if [ -n "${crit_warn:-}" ] && [ "$crit_warn" != "0x00" ]; then
        status="CRITICAL"
    elif [ -n "${media_err:-}" ] && [ "$media_err" -gt 0 ] 2>/dev/null; then
        status="WARNING (media errors present)"
    elif [ -n "${pct_used:-}" ] && [ "$pct_used" -ge "$WARN_PCT_USED" ] 2>/dev/null; then
        status="WARNING (wear level ${pct_used}%)"
    elif [ -n "${spare:-}" ] && [ "$spare" -le "$WARN_SPARE_PCT" ] 2>/dev/null; then
        status="WARNING (spare low)"
    fi

    case "$status" in
        OK) echo -e "Status:           ${GREEN}${status}${NC}" ;;
        CRITICAL) echo -e "Status:           ${RED}${status}${NC}"; overall_status=2 ;;
        *) echo -e "Status:           ${YELLOW}${status}${NC}"; [ "$overall_status" -lt 1 ] && overall_status=1 ;;
    esac
    echo
done

echo "=== Summary (${host}) ==="
case $overall_status in
    0) echo -e "${GREEN}All NVMe drives healthy.${NC}" ;;
    1) echo -e "${YELLOW}One or more drives show warning-level wear/errors — keep an eye on them.${NC}" ;;
    2) echo -e "${RED}One or more drives report a CRITICAL SMART warning — investigate now.${NC}" ;;
esac

exit $overall_status
```

## nvme-health-check-all.sh (fleet wrapper)

Pushes the script to each host and runs it, so only one copy needs to be
maintained. Fill in the remote Proxmox host's SSH alias/IP before use.

```bash
#!/usr/bin/env bash
# nvme-health-check-all.sh — run nvme-health-check.sh across the Proxmox fleet
# Copy nvme-health-check.sh to each host first (or scp it inline, see below).
set -uo pipefail

# Edit this list — fill in the remote host's SSH alias/IP.
HOSTS=(
    "root@swearengen"
    "root@wu"
    # "root@<remote-proxmox-host>"
)

REMOTE_SCRIPT_PATH="/root/nvme-health-check.sh"
LOCAL_SCRIPT="$(dirname "$0")/nvme-health-check.sh"

for h in "${HOSTS[@]}"; do
    echo "############################################"
    echo "# $h"
    echo "############################################"

    # Push the latest script version, then run it.
    if scp -q "$LOCAL_SCRIPT" "${h}:${REMOTE_SCRIPT_PATH}" 2>/dev/null; then
        ssh "$h" "chmod +x ${REMOTE_SCRIPT_PATH} && ${REMOTE_SCRIPT_PATH}"
    else
        echo "Could not reach $h — skipping."
    fi
    echo
done
```

## Usage

```bash
chmod +x nvme-health-check.sh nvme-health-check-all.sh
./nvme-health-check-all.sh
```

