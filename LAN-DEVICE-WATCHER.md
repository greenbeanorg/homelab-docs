# LAN Device Watcher — Runbook

LAN discovery and device visibility for the homelab, running as a Dockerized Node.js application on `ellsworth`.

- **Monitoring host:** `ellsworth` — Fedora Server
- **Host IP:** `10.x.x.111`
- **Interface:** `ens18`
- **LAN:** `10.x.x.0/24`
- **Container:** `lan-dev-watcher`
- **Application:** Node.js + Express
- **Database:** SQLite
- **Container networking:** Docker host networking
- **Web UI:** `http://10.x.x.111:3000`
- **Application path:** `/opt/lan-dev-watcher`
- **Persistent data:** `/opt/lan-dev-watcher/data`
- **Firewall:** `firewalld`
- **Firewall zone:** `FedoraServer`

---

## 1. Architecture

```text
                         LAN
                    10.x.x.0/24
                         |
                         v
              +----------------------+
              |      ellsworth       |
              |     10.x.x.111       |
              |                      |
              |   Fedora Server      |
              |        |             |
              |    firewalld          |
              |        |             |
              |      Docker          |
              |        |             |
              |        v             |
              |  lan-dev-watcher     |
              |      Node.js         |
              |       :3000          |
              |        |             |
              |        v             |
              |      SQLite          |
              +----------------------+
                         |
                         v
                  Web Dashboard
                      :3000
```

The watcher periodically scans `10.x.x.0/24`, discovers devices, records them in SQLite, and exposes the results through a web dashboard.

The scanner currently uses:

1. ICMP probing
2. Host ARP/neighbour table
3. Reverse DNS
4. SQLite persistence

The application runs with Docker host networking because LAN discovery requires access to the host's network namespace.

---

## 2. Files

```text
/opt/lan-dev-watcher/
├── src/
│   ├── server.js
│   ├── scanner.js
│   ├── db.js
│   └── public/
│       ├── index.html
│       ├── app.js
│       └── style.css
├── data/
│   └── devices.db
├── package.json
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
└── .gitignore
```

| File | Purpose |
|---|---|
| `src/server.js` | Express server, API, scan scheduling |
| `src/scanner.js` | LAN discovery logic |
| `src/db.js` | SQLite database and persistence |
| `src/public/index.html` | Dashboard HTML |
| `src/public/app.js` | Dashboard JavaScript |
| `src/public/style.css` | Dashboard styling |
| `package.json` | Node.js dependencies and scripts |
| `Dockerfile` | Container image definition |
| `docker-compose.yml` | Container/runtime configuration |
| `data/devices.db` | Persistent device database |

`devices.db` should not be committed to Git.

---

## 3. Docker Configuration

The container intentionally uses host networking:

```yaml
services:
  lan-dev-watcher:
    build: .

    container_name: lan-dev-watcher

    restart: unless-stopped

    network_mode: host

    environment:
      SUBNET: "10.x.x.0/24"
      PORT: "3000"
      SCAN_INTERVAL: "60000"
      DB_PATH: "/app/data/devices.db"

    volumes:
      - ./data:/app/data
```

### Why host networking?

With:

```yaml
network_mode: host
```

the container shares the host's network namespace.

The application therefore listens directly on:

```text
*:3000
```

on `ellsworth`.

Do **not** add a Docker port mapping while using host networking:

```yaml
ports:
  - "3000:3000"
```

---

## 4. Configuration

| Setting | Value | Description |
|---|---|---|
| `SUBNET` | `10.x.x.0/24` | Network to scan |
| `PORT` | `3000` | Web UI/API port |
| `SCAN_INTERVAL` | `60000` | Scan interval in milliseconds |
| `DB_PATH` | `/app/data/devices.db` | SQLite database path |

`60000` milliseconds = 60 seconds.

Only one scan may run at a time. If the scheduled scan fires while another scan is still running:

```text
Scan already running, skipping.
```

This is expected behavior.

---

## 5. Initial Deployment

```bash
cd /opt/lan-dev-watcher

docker compose up -d --build
```

Verify the container:

```bash
docker compose ps
```

Check logs:

```bash
docker compose logs --tail=100
```

Follow logs:

```bash
docker compose logs -f
```

Verify the web server:

```bash
curl -I http://localhost:3000
```

Expected:

```text
HTTP/1.1 200 OK
```

---

## 6. Web Dashboard

Dashboard:

```text
http://10.x.x.111:3000
```

From another LAN machine:

```bash
curl -I http://10.x.x.111:3000
```

The dashboard currently displays:

- Total devices
- Online devices
- Offline devices
- IP address
- Hostname
- MAC address
- Last seen
- Device status
- Manual scan control

---

## 7. Firewall

The host uses `firewalld` with zone:

```text
FedoraServer
```

Port `3000/tcp` is restricted to the homelab LAN.

### Add LAN-only rule

```bash
sudo firewall-cmd --permanent \
  --add-rich-rule='rule family="ipv4" source address="10.x.x.0/24" port port="3000" protocol="tcp" accept'

sudo firewall-cmd --reload
```

Verify:

```bash
sudo firewall-cmd --list-rich-rules
```

Expected:

```text
rule family="ipv4" source address="10.x.x.0/24" port port="3000" protocol="tcp" accept
```

Verify the firewall zone:

```bash
sudo firewall-cmd --list-all
```

Verify the listening socket:

```bash
sudo ss -lntp | grep :3000
```

Expected:

```text
LISTEN ... *:3000 ... node
```

### Security

The dashboard is intended to be LAN-only.

Do not expose TCP/3000 directly to the Internet.

The application currently has:

- No authentication
- No HTTPS
- No user management
- No role-based access
- No audit logging

For remote access, use a VPN, Tailscale, or an authenticated HTTPS reverse proxy.

---

## 8. Application API

### Device list

```bash
curl http://localhost:3000/api/devices
```

### Statistics

```bash
curl http://localhost:3000/api/stats
```

### Application status

```bash
curl http://localhost:3000/api/status
```

### Manual scan

```bash
curl -X POST http://localhost:3000/api/scan
```

Expected:

```json
{
  "message": "Scan started"
}
```

If a scan is already running, the API returns HTTP `409`. This is expected.

---

## 9. Scanner

Each scan:

1. Generates host addresses from the configured subnet.
2. Probes hosts using ICMP.
3. Reads the host ARP/neighbour table.
4. Attempts reverse DNS resolution.
5. Records discovered devices in SQLite.
6. Updates `last_seen`.
7. Marks previously known devices offline when they are not discovered during the current scan.

The current subnet is:

```text
10.x.x.0/24
```

This provides 254 usable IPv4 host addresses.

### Scanner concurrency

Hosts are processed in batches.

Current concurrency:

```text
32 hosts
```

This prevents all 254 addresses from being probed simultaneously.

---

## 10. Normal Operations

### Start

```bash
cd /opt/lan-dev-watcher
docker compose up -d
```

### Stop

```bash
cd /opt/lan-dev-watcher
docker compose down
```

### Restart

```bash
cd /opt/lan-dev-watcher
docker compose restart
```

### Status

```bash
cd /opt/lan-dev-watcher
docker compose ps
```

### Logs

```bash
cd /opt/lan-dev-watcher
docker compose logs --tail=100
```

### Follow logs

```bash
cd /opt/lan-dev-watcher
docker compose logs -f
```

### Manual scan

```bash
curl -X POST http://localhost:3000/api/scan
```

---

## 11. Updating the Application

After changing source files:

```bash
cd /opt/lan-dev-watcher

docker compose up -d --build
```

Verify:

```bash
docker compose ps
docker compose logs --tail=100
curl -I http://localhost:3000
curl -s http://localhost:3000/api/status
```

A normal restart is **not sufficient** after changing application source:

```bash
docker compose restart
```

The source is copied into the Docker image during the build, so an image rebuild is required.

---

## 12. Rebuild From Scratch

For a clean image rebuild:

```bash
cd /opt/lan-dev-watcher

docker compose down

docker compose build --no-cache

docker compose up -d
```

The database should remain intact because it is stored outside the container:

```text
/opt/lan-dev-watcher/data/devices.db
```

Verify:

```bash
ls -lh /opt/lan-dev-watcher/data/
```

Expected:

```text
devices.db
```

---

## 13. Upgrade Procedure

Back up the database first:

```bash
cd /opt/lan-dev-watcher

cp data/devices.db \
  "data/devices-$(date +%F-%H%M%S).db"
```

Update the application source using the normal Git workflow.

Rebuild:

```bash
docker compose up -d --build
```

Verify:

```bash
docker compose ps
docker compose logs --tail=100
curl -I http://localhost:3000
curl -s http://localhost:3000/api/status
```

---

## 14. Rollback

If an update causes problems and the previous version is available in Git:

```bash
cd /opt/lan-dev-watcher

git log --oneline -10
```

Return to the known-good commit:

```bash
git checkout <known-good-commit>
```

Rebuild:

```bash
docker compose up -d --build
```

Verify:

```bash
docker compose ps
docker compose logs --tail=100
curl -I http://localhost:3000
```

The SQLite database normally does not need to be rolled back unless the application update changed the database schema.

---

## 15. Backup

Primary persistent state:

```text
/opt/lan-dev-watcher/data/devices.db
```

### Database backup

```bash
cd /opt/lan-dev-watcher

cp data/devices.db \
  "data/devices-$(date +%F-%H%M%S).db"
```

### Compressed backup

```bash
cd /opt/lan-dev-watcher

gzip -c data/devices.db \
  > "data/devices-$(date +%F-%H%M%S).db.gz"
```

The application directory should be included in the normal homelab backup system.

Generated directories such as `node_modules/` do not need to be backed up.

Source code and Docker configuration belong in Git.

---

## 16. Database Restore

Stop the application:

```bash
cd /opt/lan-dev-watcher
docker compose down
```

Restore the desired backup:

```bash
cp data/devices-YYYY-MM-DD-HHMMSS.db \
  data/devices.db
```

Start the application:

```bash
docker compose up -d
```

Verify:

```bash
docker compose logs --tail=50
curl -s http://localhost:3000/api/stats
```

---

## 17. Database Inspection

If `sqlite3` is installed:

```bash
sqlite3 /opt/lan-dev-watcher/data/devices.db
```

List devices:

```sql
SELECT
  ip,
  hostname,
  mac,
  online,
  first_seen,
  last_seen
FROM devices
ORDER BY ip;
```

Count devices:

```sql
SELECT COUNT(*) FROM devices;
```

Show online devices:

```sql
SELECT
  ip,
  hostname,
  mac
FROM devices
WHERE online = 1
ORDER BY ip;
```

Exit:

```sql
.quit
```

Install SQLite on Fedora if required:

```bash
sudo dnf install sqlite
```

---

## 18. Troubleshooting

| Symptom | Check |
|---|---|
| Dashboard returns 404 | Verify `src/public/index.html`; rebuild image |
| Port 3000 not listening | `ss`, `docker compose ps`, logs |
| Works locally but not remotely | Firewall, VLAN/ACL, interface configuration |
| Container running but Node inaccessible | Verify host networking and listening socket |
| Scanner appears stuck | Follow application logs |
| No devices discovered | Verify subnet, connectivity, neighbour table |
| Known device missing | ICMP, firewall, sleep state, VLAN/isolation, ARP |
| `docker exec ... ps` fails | Slim Node image does not include `ps` |

### Dashboard returns 404

```bash
ls -la /opt/lan-dev-watcher/src/public
```

Expected:

```text
index.html
app.js
style.css
```

Rebuild:

```bash
docker compose up -d --build
```

### Port 3000 is not listening

```bash
sudo ss -lntp | grep :3000
docker compose ps
docker compose logs --tail=100
curl -I http://localhost:3000
```

### Works on localhost but not from another LAN host

Check the interface:

```bash
ip -br addr
```

Expected:

```text
ens18    UP    10.x.x.111/24
```

Check the firewall:

```bash
sudo firewall-cmd --list-all
sudo firewall-cmd --list-rich-rules
```

From another LAN machine:

```bash
curl -v http://10.x.x.111:3000
```

Investigate, in order:

1. `firewalld`
2. Network ACLs
3. VLAN routing
4. Client isolation
5. Host/interface configuration

### Verify host networking

```bash
docker inspect lan-dev-watcher \
  --format '{{.HostConfig.NetworkMode}}'
```

Expected:

```text
host
```

Then:

```bash
sudo ss -lntp | grep :3000
```

---

## 19. Scanner Troubleshooting

### Scanner appears stuck

```bash
docker compose logs -f
```

The scanner performs ICMP probes and reverse DNS lookups, so scans can take longer than expected.

If the next scheduled scan fires before the previous scan finishes:

```text
Scan already running, skipping.
```

This is expected.

### No devices are discovered

Verify the configured subnet:

```bash
docker exec lan-dev-watcher \
  node -e 'console.log(process.env.SUBNET)'
```

Expected:

```text
10.x.x.0/24
```

Test LAN connectivity:

```bash
ping -c 2 10.x.x.1
```

Check the neighbour table:

```bash
ip neigh
```

Check application logs:

```bash
docker compose logs --tail=100
```

### Known device does not appear

The current scanner relies primarily on ICMP.

Possible causes:

- ICMP blocked
- Host firewall
- Device sleeping
- Wireless client isolation
- VLAN separation
- No current ARP/neighbour entry
- DNS timeout

**Important:** absence from the dashboard does not necessarily mean the device is physically offline.

---

## 20. Security Considerations

The dashboard currently uses plain HTTP:

```text
http://10.x.x.111:3000
```

There is no authentication or HTTPS.

The primary access restriction is therefore the Fedora firewall.

The container also uses host networking:

```yaml
network_mode: host
```

This is intentional for LAN discovery but gives the container greater network visibility than a normal Docker bridge container.

Treat the application accordingly.

Do not port-forward TCP/3000 to the Internet.

---

## 21. Monitoring Architecture

The watcher runs on `ellsworth`, which is itself part of the monitored LAN:

```text
ellsworth fails
     |
     v
LAN Device Watcher fails
     |
     v
No local monitoring
```

Therefore the watcher cannot report its own host being offline.

For higher reliability, future architecture could include:

```text
Watcher A  --->  External heartbeat
                      |
                      v
                Alert if absent
```

or:

```text
Watcher A  --->  Monitors LAN
Watcher B  --->  Monitors Watcher A
```

---

## 22. VLAN Considerations

The current configuration assumes:

```text
10.x.x.0/24
```

is the relevant LAN.

ARP discovery does not cross routed Layer-3 boundaries.

For example:

```text
VLAN 10   Servers
VLAN 20   Clients
VLAN 30   IoT
VLAN 40   Management
```

A watcher on VLAN 10 cannot perform normal Layer-2 ARP discovery of devices on VLAN 20.

Multi-VLAN discovery will require routed discovery mechanisms and appropriate firewall rules.

---

## 23. Known Limitations

Current limitations:

1. ICMP is the primary discovery mechanism.
2. Devices that block ping may not appear.
3. MAC addresses depend on the host ARP/neighbour table.
4. Vendor identification is not implemented.
5. Reverse DNS can slow scans.
6. A scan can take longer than the configured scan interval.
7. Only one scan runs at a time.
8. There is no authentication.
9. There is no HTTPS.
10. There is no historical latency graphing.
11. There is no device naming UI.
12. There is no device notes/tags system.
13. There is no SNMP integration.
14. There is no TCP service discovery.
15. There is no VLAN-aware discovery.
16. The monitoring host is itself part of the monitored network.
17. If `ellsworth` fails, the watcher fails with it.

---

## 24. Planned Improvements

### Discovery

- ARP/neighbour discovery
- mDNS discovery
- Optional `nmap` integration
- TCP port detection
- Better hostname discovery
- MAC vendor/OUI lookup
- IPv6 discovery

### Dashboard

- Device search
- Device filtering
- Sortable columns
- Device detail page
- Custom device names
- Device notes
- Device groups
- VLAN/subnet grouping
- Device history

### Monitoring

- Latency history
- Online/offline history
- Uptime percentage
- Historical graphs
- Configurable scan intervals
- Per-device monitoring
- Alerting

### Homelab integrations

- Docker container discovery
- Proxmox API
- OPNsense
- UniFi
- SNMP
- ntfy
- Webhooks

### Security

- Authentication
- HTTPS/reverse proxy
- Role-based access
- LAN/VLAN access controls

---

## 25. Testing Checklist

Run after deployment, upgrade, or recovery.

### Container

```bash
docker compose ps
```

### Logs

```bash
docker compose logs --tail=50
```

### Listening port

```bash
sudo ss -lntp | grep :3000
```

### Local HTTP

```bash
curl -I http://localhost:3000
```

### API status

```bash
curl -s http://localhost:3000/api/status
```

### Device API

```bash
curl -s http://localhost:3000/api/devices
```

### Manual scan

```bash
curl -X POST http://localhost:3000/api/scan
```

### Firewall

```bash
sudo firewall-cmd --list-rich-rules
```

### Network interface

```bash
ip -br addr
```

### ARP/neighbour table

```bash
ip neigh
```

### Remote LAN test

From another LAN machine:

```bash
curl -I http://10.x.x.111:3000
```

Then open:

```text
http://10.x.x.111:3000
```

---

## 26. Quick Recovery

If the dashboard suddenly stops working:

```bash
cd /opt/lan-dev-watcher

docker compose ps

docker compose logs --tail=100

sudo ss -lntp | grep :3000

curl -I http://localhost:3000

sudo firewall-cmd --list-rich-rules
```

If the container is missing or unhealthy:

```bash
docker compose up -d --build
```

If the application still fails:

```bash
docker compose down

docker compose build --no-cache

docker compose up -d

docker compose logs --tail=100
```

**Do not delete:**

```text
/opt/lan-dev-watcher/data/devices.db
```

unless intentionally resetting all stored device data.

---

## Quick Reference

| Item | Value |
|---|---|
| Host | `ellsworth` |
| Host IP | `10.x.x.111` |
| Interface | `ens18` |
| LAN | `10.x.x.0/24` |
| Container | `lan-dev-watcher` |
| Runtime | Node.js |
| Framework | Express |
| Database | SQLite |
| Networking | `host` |
| Web UI | `http://10.x.x.111:3000` |
| Application path | `/opt/lan-dev-watcher` |
| Database | `/opt/lan-dev-watcher/data/devices.db` |
| Port | `3000/tcp` |
| Firewall | `firewalld` |
| Firewall zone | `FedoraServer` |
| Firewall scope | `10.x.x.0/24` |
| Scan interval | 60 seconds |
| Scan concurrency | 32 hosts |
| Compose file | `docker-compose.yml` |

---

## Most-Used Commands

```bash
cd /opt/lan-dev-watcher

# Container status
docker compose ps

# Application logs
docker compose logs --tail=100

# Follow logs
docker compose logs -f

# Restart
docker compose restart

# Rebuild and restart
docker compose up -d --build

# Stop
docker compose down

# HTTP test
curl -I http://localhost:3000

# API status
curl -s http://localhost:3000/api/status

# Device list
curl -s http://localhost:3000/api/devices

# Trigger manual scan
curl -X POST http://localhost:3000/api/scan

# Check listening port
sudo ss -lntp | grep :3000

# Check firewall
sudo firewall-cmd --list-all

# Check LAN firewall rules
sudo firewall-cmd --list-rich-rules

# Check network interfaces
ip -br addr

# Check ARP/neighbour table
ip neigh
```

---

## Change Log

### 2026-08

Initial deployment.

- Created Dockerized Node.js LAN Device Watcher.
- Configured `10.x.x.0/24` discovery.
- Deployed on `ellsworth`.
- Configured host networking.
- Configured Fedora `firewalld`.
- Restricted TCP/3000 to the LAN.
- Added SQLite persistence.
- Added web dashboard.
- Added scheduled scanning.
- Added manual scan endpoint.
- Added operational runbook.
