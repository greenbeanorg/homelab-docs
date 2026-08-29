# NetBox — Docker Deployment on Fedora 44

**Status:** Complete
**Host:** ellsworth — Fedora 44
**Date:** August 28, 2026
**Application:** NetBox 4.6
**Deployment:** Docker / Docker Compose

Deployment of NetBox using the community-maintained `netbox-docker` project on Fedora 44.

The deployment runs NetBox, PostgreSQL, and Valkey through Docker Compose. The installation lives at `/opt/netbox`.

This document records the deployment, configuration validation, startup behavior, troubleshooting, and final state.

## Host

| | |
|---|---|
| Host | ellsworth |
| OS | Fedora 44 |
| Docker | 29.7.2 |
| Docker Compose | 5.5.0 |
| Install path | /opt/netbox |
| NetBox | 4.6 |
| NetBox Docker | 5.0.2 |
| Host port | 8000 |
| Container port | 8080 |

## 1. Initial environment

Docker and Docker Compose were already installed on the Fedora 44 host.

Verified with:

```bash
cat /etc/redhat-release
docker --version
docker compose version
```

Result:

```
Fedora release 44 (Forty Four)
Docker version 29.7.2, build a7dcaa6
Docker Compose version v5.5.0
```

The installed Docker and Compose versions are sufficient for the NetBox Docker deployment.

## 2. Deployment strategy

Chosen strategy: use the community `netbox-docker` repository and its `release` branch.

The deployment is intentionally containerized rather than installing NetBox directly into the Fedora host. This keeps the application, database, and Redis-compatible services isolated and allows the complete deployment to be managed through Docker Compose.

Repository: https://github.com/netbox-community/netbox-docker

## 3. Repository installation

The deployment directory was created under `/opt` and the NetBox Docker repository cloned directly into `/opt/netbox`:

```bash
cd /opt
git clone -b release https://github.com/netbox-community/netbox-docker.git netbox
cd /opt/netbox
```

The example Docker Compose override was copied:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```

Resulting layout includes:

```
/opt/netbox/
├── docker-compose.yml
├── docker-compose.override.yml
├── env/
│   ├── netbox.env
│   ├── postgres.env
│   ├── redis-cache.env
│   └── redis.env
└── ...
```

## 4. Repository verification

Verified the checked-out branch:

```bash
git branch --show-current
```

Result: `release`

Verified the current revision:

```bash
git log -1 --oneline
```

Result:

```
5adc62f (HEAD -> release, tag: 5.0.2, origin/release, origin/HEAD) Merge pull request #1719 from netbox-community/develop
```

The deployment therefore used:

- NetBox Docker release: **5.0.2**
- NetBox image: **v4.6-5.0.2**

## 5. Compose configuration validation

Before starting the stack, Docker Compose configuration was validated:

```bash
docker compose config --quiet
echo "exit=$?"
```

Result: `exit=0`

This confirmed that the Compose configuration could be parsed and resolved successfully before deployment.

## 6. Container deployment

The required images were pulled and the stack started:

```bash
docker compose pull
docker compose up -d
```

The deployment consists of:

- netbox
- netbox-worker
- postgres
- redis
- redis-cache

The supporting database and cache services are managed by Docker Compose.

## 7. Initial startup issue

During initial startup, Docker Compose reported:

```
dependency netbox failed to start
```

This initially appeared to indicate a failed deployment.

Rather than removing the containers or volumes, the container state and application logs were inspected:

```bash
docker compose ps -a
docker compose logs --tail=200 netbox
```

The container state showed that NetBox had subsequently completed initialization:

```
netbox-netbox-1          Up (healthy)
netbox-postgres-1        Up (healthy)
netbox-redis-1           Up (healthy)
netbox-redis-cache-1     Up (healthy)
```

The `netbox-worker` container remained in `Created` state at the time of this check.

The initial dependency message was therefore transient and occurred during the application's initialization period.

## 8. Database initialization

NetBox successfully completed its database migrations.

The logs showed the migrations completing with `OK`, including the final VPN and wireless migrations. Example:

```
Applying vpn.0012_alter_ikepolicy_mode... OK
Applying wireless.0020_alter_wirelesslan__region_and_more... OK
```

NetBox then reported:

```
Finished.
Initialisation is done.
```

This confirmed that PostgreSQL connectivity and the initial NetBox schema were functioning.

## 9. Search index initialization

NetBox successfully initialized its search index:

```
Reindexing 93 models.
...
Completed. Total entries: 7
```

No application models contained user-created objects yet, so most indexes reported `No objects found.` This is expected for a new installation.

## 10. Web application startup

NetBox successfully started Granian:

```
[INFO] Starting granian (main PID: 7)
[INFO] Listening at: http://:::8080
[INFO] Spawning worker-1
[INFO] Spawning worker-2
[INFO] Spawning worker-3
[INFO] Spawning worker-4
```

The application subsequently handled login requests successfully:

```
"GET /login/ HTTP/1.1" 200
```

This confirmed that the application was not merely running as a container but was serving the NetBox web interface.

## 11. Port mapping

Docker exposed NetBox's internal port 8080 as host port 8000:

```
0.0.0.0:8000 -> 8080/tcp
[::]:8000 -> 8080/tcp
```

The application is therefore reachable at:

```
http://<ellsworth-ip>:8000/
```

The login page is `http://<ellsworth-ip>:8000/login/`.

Local verification:

```bash
curl -I http://127.0.0.1:8000/
```

## 12. Superuser

The initial startup log reported:

```
Skip creating the superuser
```

This is expected when the automatic superuser configuration is not enabled.

The initial administrator can be created manually:

```bash
docker compose exec netbox \
  /opt/netbox/netbox/manage.py createsuperuser
```

The command prompts for `Username`, `Email address`, and `Password` (twice). No credentials are recorded in this document.

## 13. Configuration warning

The NetBox logs reported:

```
FutureWarning: LOGIN_REQUIRED is deprecated and will be removed in NetBox v5.0.
```

This is a deprecation warning rather than an application failure. NetBox continued to initialize and serve requests normally. The deprecated configuration should be removed during a future configuration cleanup.

## 14. Current container state

At verification time:

```
NAME                     STATUS
netbox-netbox-1          Up 4 minutes (healthy)
netbox-netbox-worker-1   Created
netbox-postgres-1        Up 4 minutes (healthy)
netbox-redis-1           Up 4 minutes (healthy)
netbox-redis-cache-1     Up 4 minutes (healthy)
```

The primary NetBox application, PostgreSQL, Redis-compatible services, and health checks were operational.

## 15. Operational commands

**Show container status**
```bash
cd /opt/netbox
docker compose ps -a
```

**Follow NetBox logs**
```bash
docker compose logs -f netbox
```

**Show recent NetBox logs**
```bash
docker compose logs --tail=200 netbox
```

**Show PostgreSQL logs**
```bash
docker compose logs --tail=100 postgres
```

**Show Redis logs**
```bash
docker compose logs --tail=100 redis
```

**Show Redis cache logs**
```bash
docker compose logs --tail=100 redis-cache
```

**Restart the stack**
```bash
docker compose restart
```

**Stop the stack**
```bash
docker compose down
```

**Start the stack**
```bash
docker compose up -d
```

**Pull updated images**
```bash
docker compose pull
```

> Do not blindly update the Git repository and container images independently. Keep the NetBox Docker repository release and container image version synchronized.

## 16. Troubleshooting

If Docker Compose reports:

```
dependency netbox failed to start
```

first inspect the actual container state:

```bash
docker compose ps -a
```

Then inspect NetBox:

```bash
docker compose logs --tail=200 netbox
```

If necessary, inspect the supporting services:

```bash
docker compose logs --tail=100 postgres
docker compose logs --tail=100 redis
docker compose logs --tail=100 redis-cache
```

Do not immediately delete containers or volumes.

Initial NetBox startup performs database migrations, initialization tasks, and search indexing. The application can therefore take some time to become healthy.

The authoritative checks are `docker compose ps -a` and `docker compose logs --tail=200 netbox`.

A healthy NetBox container combined with successful HTTP responses from `/login/` indicates that the deployment completed successfully.

## 17. Verification

Basic Docker verification:
```bash
docker --version
docker compose version
```

Compose validation:
```bash
docker compose config --quiet
```

Container verification:
```bash
docker compose ps -a
```

Application verification:
```bash
docker compose logs --tail=50 netbox
```

HTTP verification:
```bash
curl -I http://127.0.0.1:8000/
```

Expected application behavior: `GET /login/` → HTTP 200

## 18. Production hardening — TODO

The initial deployment is complete, but the following should be addressed before treating this as a production service:

- [ ] Create the initial NetBox administrator
- [ ] Generate and document a permanent SECRET_KEY
- [ ] Review PostgreSQL credentials
- [ ] Review Valkey/Redis credentials
- [ ] Configure appropriate ALLOWED_HOSTS
- [ ] Review Docker port exposure
- [ ] Configure firewall rules
- [ ] Put NetBox behind a reverse proxy
- [ ] Configure HTTPS/TLS
- [ ] Configure DNS
- [ ] Configure persistent storage/backup procedures
- [ ] Establish PostgreSQL backup procedures
- [ ] Establish NetBox media backup procedures
- [ ] Configure container restart policies
- [ ] Configure monitoring/health checks
- [ ] Document upgrade procedure
- [ ] Document rollback procedure
- [ ] Remove deprecated LOGIN_REQUIRED configuration
- [ ] Document NetBox Docker image/repository version pairing

## Key lessons

- A Docker Compose dependency error during initial startup does not necessarily mean the deployment failed.
- Check `docker compose ps -a` and the application logs before taking destructive action.
- NetBox performs substantial initialization work during first startup, including database migrations and search indexing.
- `docker compose config --quiet` is useful for validating the deployment before starting containers.
- The NetBox web application was confirmed operational by successful `GET /login/` HTTP 200 responses.
- The initial superuser was not automatically created and must be created manually.
- The `LOGIN_REQUIRED` message is a deprecation warning and did not prevent NetBox from operating.
- Port 8000 is suitable for initial testing; production deployment should use HTTPS and normally place NetBox behind a reverse proxy.
- NetBox Docker repository and image versions should be kept synchronized.

## Final state

```
                         ellsworth
                       Fedora 44
                           │
                    Docker 29.7.2
                           │
                  Docker Compose 5.5.0
                           │
                    /opt/netbox
                           │
             ┌─────────────┴─────────────┐
             │                           │
       NetBox Docker 5.0.2        Docker Compose
             │                           │
       NetBox v4.6                       │
             │              ┌────────────┼───────────┐
             │              │            │           │
          :8080         PostgreSQL     Valkey      Valkey
             │             :5432        :6379       :6379
             │                           │
             └────────── host :8000 ─────┘
```

NetBox is installed and operational on ellsworth.

The application completed database initialization, search indexing, web worker startup, container health checks, and successful HTTP requests.

**Status: COMPLETE — initial deployment**

Production hardening remains outstanding.

## References

- NetBox Docker repository: https://github.com/netbox-community/netbox-docker
- NetBox Docker release documentation: https://github.com/netbox-community/netbox-docker/blob/release/README.md
- NetBox Docker deployment wiki: https://github.com/netbox-community/netbox-docker/wiki

---

