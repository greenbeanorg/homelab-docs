# NetBox — Inventory Population

**Status:** Ready to run
**Host:** NetBox on `ellsworth:8000` (see [NETBOX.md](NETBOX.md) for the deployment)
**Files:** `scripts/netbox_inventory.yaml`, `scripts/netbox_sync.py`, `scripts/requirements.txt`

Populates the NetBox instance deployed in [NETBOX.md](NETBOX.md) with the
current state of the lab. Same pattern as the Uptime Kuma setup in
[UPTIME-KUMA.md](UPTIME-KUMA.md): `netbox_inventory.yaml` is the declared
state, `netbox_sync.py` reconciles NetBox to match it, joined by name/slug.

## 1. Data model

| NetBox object | Source |
| --- | --- |
| Sites | Home (Ormond Beach), Friend's LAN (remote), Oracle Cloud (`kk1`) |
| Physical devices | `swearengen`, `wu`, the remote Proxmox host, the ODROID-XU4 Pi-hole, the CRS310, the EAP610, the M4 Mac |
| Clusters | One Proxmox-VE cluster per hypervisor (not an actual PVE cluster — used here purely to group each host's guests), plus a Cloud cluster for `kk1` |
| VMs/LXCs | ~25 guests across `swearengen`, `wu`, and the remote host |
| Prefixes | `10.x.x.0/24` (home LAN), `10.99.x.0/24` (WireGuard overlay), `10.x.179.0/24` (`kk1` VCN), `192.168.x.0/24` (friend's LAN) |

## 2. How the sync works

- **Reconciler, not a one-shot loader.** `netbox_sync.py` reads
  `netbox_inventory.yaml` top to bottom and, for each object, does a
  get-or-create against NetBox — same CREATE/UPDATE/OK reporting as
  `kuma_sync.py`.
- **Join key is name/slug.** Renaming an entry in the YAML creates a new
  object rather than renaming the existing one — same caveat as Kuma's
  monitor `name` field.
- **Non-destructive.** Creates and updates only, never deletes. An object
  present in NetBox but absent from the YAML is left alone — no
  `UNMANAGED` reporting like Kuma has, since NetBox holds far more object
  types than this script manages; anything outside the YAML's scope
  (VLANs, cables, racks, etc.) simply isn't touched.
- **Fail-fast on bad references.** If a device references a role/site/
  platform that isn't defined earlier in the YAML, the script raises
  immediately rather than silently skipping it — same philosophy as
  Kuma's `TYPE_MAP` validation.
- **`--dry-run` is supported and should be run first** — but only fully
  accurate once the referenced parent objects (sites, manufacturers,
  roles, platforms, clusters) already exist in NetBox. On a brand-new
  instance, the first pass has to be a real run; `--dry-run` is for
  reviewing edits after that.

## 3. Prerequisites

- NetBox reachable and logged in at `http://ellsworth:8000`
- An API token: **Admin → API Tokens → Add a token** (write-enabled)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Run

```bash
export NETBOX_URL="http://ellsworth:8000"
export NETBOX_TOKEN="<token from step 3>"
python3 netbox_sync.py --dry-run     # always first
python3 netbox_sync.py               # apply
```

## 5. Add or change something

1. Edit `netbox_inventory.yaml` — add a device/VM/prefix, or change a
   field on an existing one (role, platform, comments, primary_ip, …).
2. `python3 netbox_sync.py --dry-run` — confirm the expected `CREATE`/
   `UPDATE` lines and nothing unexpected.
3. `python3 netbox_sync.py`.

## 6. Known gaps to fill in by hand

- IPs for `stubbs`, `cochran`, `sol`, `jane`, `trixie`, `nuttal`, `utter`,
  `dority`, and the other VMs left with no `primary_ip` in the YAML —
  add the key once you have the address, then re-run
- `kk1`'s actual host address on the `10.x.179.0/24` VCN subnet — the
  network ID itself can't be assigned to an interface
- Real hostname for the remote Proxmox host (currently `remote-pve`) and
  the friend's-LAN Pi-hole VM (currently `friend-pihole`), once confirmed
- Device serials/asset tags, if tracking those matters
- VLANs — intentionally out of scope; the flat `10.x.x.0/24` is what's
  live today, and the planned VLAN segmentation onto `10.79.x.x` is its
  own pass once that renumbering actually happens

## Quick reference

| | |
| --- | --- |
| NetBox URL | `http://ellsworth:8000` |
| Inventory file | `scripts/netbox_inventory.yaml` |
| Reconciler | `scripts/netbox_sync.py`, `--dry-run` supported |
| Join key | name/slug per object type — rename = new object |
| Deletes | Never — objects outside the YAML are left alone |
| Token scope needed | Write-enabled (Admin → API Tokens) |

---

