#!/usr/bin/env python3
"""
netbox_sync.py — reconcile NetBox against netbox_inventory.yaml
=================================================================
Same pattern as ~/homelab-monitoring/uptime-kuma/kuma_sync.py:
inventory.yaml is the declared state, this script reconciles NetBox to
match it, joined by name/slug. Non-destructive — it creates and updates,
never deletes. Objects in NetBox but absent from the YAML are left alone.

Usage:
    export NETBOX_URL="http://ellsworth:8000"
    export NETBOX_TOKEN="<Admin > API Tokens in the NetBox UI>"
    python3 netbox_sync.py --dry-run     # always run this first
    python3 netbox_sync.py               # apply

Output legend (same as kuma_sync.py):
    CREATE      In the YAML, not in NetBox
    UPDATE      Exists, but a tracked field drifted from the YAML
    OK          No drift
    (nothing)   In NetBox, not in the YAML — never touched

Known limitation: --dry-run is only fully accurate when the parent objects
a new item depends on (site, manufacturer, role, platform, cluster) already
exist in NetBox. On a brand-new instance, run once for real, then use
--dry-run for subsequent edits to the YAML.
"""

import argparse
import os
import sys

import pynetbox
import yaml

INVENTORY_FILE = "netbox_inventory.yaml"

NETBOX_URL = os.environ.get("NETBOX_URL", "http://ellsworth:8000")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN")


# ---------------------------------------------------------------------------
# Core reconciliation primitive
# ---------------------------------------------------------------------------

def sync_item(label, existing, target_fields, create_fn, dry_run):
    """
    existing:       pynetbox Record or None
    target_fields:  dict of field -> desired value (already resolved to IDs)
    create_fn:      callable() -> new Record; not called under --dry-run
    Returns the resulting Record (existing/updated), or None if newly
    created under --dry-run (nothing to return yet).
    """
    if existing is None:
        print(f"  CREATE  {label}")
        if dry_run:
            return None
        return create_fn()

    diffs = {}
    for field, want in target_fields.items():
        have = getattr(existing, field, None)
        have_val = have.id if hasattr(have, "id") else have
        if have_val != want:
            diffs[field] = want

    if diffs:
        print(f"  UPDATE  {label}  ({', '.join(diffs)})")
        if not dry_run:
            existing.update(diffs)
    else:
        print(f"  OK      {label}")
    return existing


def ensure_interface(dev_or_vm, name, is_vm):
    endpoint = nb.virtualization.interfaces if is_vm else nb.dcim.interfaces
    key = "virtual_machine_id" if is_vm else "device_id"
    existing = list(endpoint.filter(**{key: dev_or_vm.id, "name": name}))
    if existing:
        return existing[0]
    payload = {"name": name}
    if is_vm:
        payload["virtual_machine"] = dev_or_vm.id
    else:
        payload["device"] = dev_or_vm.id
        payload["type"] = "1000base-t"
    return endpoint.create(payload)


def assign_primary_ip(obj, iface, is_vm, cidr):
    if not cidr:
        return
    assigned_type = "virtualization.vminterface" if is_vm else "dcim.interface"
    ip = nb.ipam.ip_addresses.get(address=cidr)
    if not ip:
        ip = nb.ipam.ip_addresses.create({
            "address": cidr,
            "assigned_object_type": assigned_type,
            "assigned_object_id": iface.id,
        })
    elif not ip.assigned_object_id:
        ip.update({"assigned_object_type": assigned_type, "assigned_object_id": iface.id})
    if getattr(getattr(obj, "primary_ip4", None), "id", None) != ip.id:
        obj.update({"primary_ip4": ip.id})


def require(mapping, key, label):
    if key not in mapping:
        raise ValueError(f"{label} references '{key}', which isn't defined earlier in the YAML")
    return mapping[key]


# ---------------------------------------------------------------------------
# Section syncers
# ---------------------------------------------------------------------------

def sync_sites(data, dry_run):
    print("== Sites ==")
    out = {}
    for s in data.get("sites", []):
        existing = nb.dcim.sites.get(slug=s["slug"])
        target = {"name": s["name"], "status": s.get("status", "active")}
        obj = sync_item(f"site:{s['slug']}", existing, target,
                         lambda s=s, t=target: nb.dcim.sites.create(slug=s["slug"], **t), dry_run)
        out[s["slug"]] = obj
    return out


def sync_manufacturers(data, dry_run):
    print("== Manufacturers ==")
    out = {}
    for m in data.get("manufacturers", []):
        existing = nb.dcim.manufacturers.get(slug=m["slug"])
        target = {"name": m["name"]}
        obj = sync_item(f"manufacturer:{m['slug']}", existing, target,
                         lambda m=m, t=target: nb.dcim.manufacturers.create(slug=m["slug"], **t), dry_run)
        out[m["slug"]] = obj
    return out


def sync_device_roles(data, dry_run):
    print("== Device roles ==")
    out = {}
    for r in data.get("device_roles", []):
        existing = nb.dcim.device_roles.get(slug=r["slug"])
        target = {"name": r["name"], "color": r.get("color", "9e9e9e"), "vm_role": True}
        obj = sync_item(f"role:{r['slug']}", existing, target,
                         lambda r=r, t=target: nb.dcim.device_roles.create(slug=r["slug"], **t), dry_run)
        out[r["slug"]] = obj
    return out


def sync_platforms(data, dry_run):
    print("== Platforms ==")
    out = {}
    for p in data.get("platforms", []):
        existing = nb.dcim.platforms.get(slug=p["slug"])
        target = {"name": p["name"]}
        obj = sync_item(f"platform:{p['slug']}", existing, target,
                         lambda p=p, t=target: nb.dcim.platforms.create(slug=p["slug"], **t), dry_run)
        out[p["slug"]] = obj
    return out


def sync_cluster_types(data, dry_run):
    print("== Cluster types ==")
    out = {}
    for c in data.get("cluster_types", []):
        existing = nb.virtualization.cluster_types.get(slug=c["slug"])
        target = {"name": c["name"]}
        obj = sync_item(f"cluster-type:{c['slug']}", existing, target,
                         lambda c=c, t=target: nb.virtualization.cluster_types.create(slug=c["slug"], **t), dry_run)
        out[c["slug"]] = obj
    return out


def sync_device_types(data, mfrs, dry_run):
    print("== Device types ==")
    out = {}
    for dt in data.get("device_types", []):
        mfr = require(mfrs, dt["manufacturer"], f"device_type:{dt['slug']}")
        mfr_id = mfr.id if mfr else None
        # NetBox's device-types filter matches `manufacturer` by slug, not
        # ID — `manufacturer_id` is the numeric-ID filter.
        existing = nb.dcim.device_types.get(slug=dt["slug"], manufacturer_id=mfr_id) if mfr_id else None
        target = {"model": dt["model"], "manufacturer": mfr_id}
        obj = sync_item(
            f"device-type:{dt['slug']}", existing, target,
            lambda dt=dt, mfr_id=mfr_id: nb.dcim.device_types.create(
                slug=dt["slug"], model=dt["model"], manufacturer=mfr_id, u_height=0),
            dry_run,
        )
        out[dt["slug"]] = obj
    return out


def sync_devices(data, dtypes, roles, sites, platforms, dry_run):
    print("== Physical devices ==")
    out = {}
    for d in data.get("devices", []):
        dtype = require(dtypes, d["device_type"], f"device:{d['name']}")
        role = require(roles, d["role"], f"device:{d['name']}")
        site = require(sites, d["site"], f"device:{d['name']}")
        plat = platforms.get(d["platform"]) if d.get("platform") else None
        target = {
            "device_type": dtype.id if dtype else None,
            "role": role.id if role else None,
            "site": site.id if site else None,
            "platform": plat.id if plat else None,
            "comments": d.get("comments", ""),
        }
        existing = nb.dcim.devices.get(name=d["name"])
        obj = sync_item(
            f"device:{d['name']}", existing, target,
            lambda d=d, t=target: nb.dcim.devices.create(name=d["name"], status="active", **t),
            dry_run,
        )
        out[d["name"]] = obj
        if obj and d.get("primary_ip"):
            iface = ensure_interface(obj, d.get("interface", "eth0"), is_vm=False)
            assign_primary_ip(obj, iface, is_vm=False, cidr=d["primary_ip"])
    return out


def sync_clusters(data, ctypes, sites, dry_run):
    print("== Clusters ==")
    out = {}
    for c in data.get("clusters", []):
        ctype = require(ctypes, c["type"], f"cluster:{c['name']}")
        site = require(sites, c["site"], f"cluster:{c['name']}")
        target = {"type": ctype.id if ctype else None, "site": site.id if site else None}
        existing = nb.virtualization.clusters.get(name=c["name"])
        obj = sync_item(
            f"cluster:{c['name']}", existing, target,
            lambda c=c, t=target: nb.virtualization.clusters.create(name=c["name"], **t),
            dry_run,
        )
        out[c["name"]] = obj
    return out


def sync_vms(data, clusters, roles, platforms, dry_run):
    print("== Virtual machines ==")
    for v in data.get("virtual_machines", []):
        cluster = require(clusters, v["cluster"], f"vm:{v['name']}")
        role = require(roles, v["role"], f"vm:{v['name']}")
        plat = platforms.get(v["platform"]) if v.get("platform") else None
        target = {
            "cluster": cluster.id if cluster else None,
            "role": role.id if role else None,
            "platform": plat.id if plat else None,
            "comments": v.get("comments", ""),
        }
        cluster_id = cluster.id if cluster else None
        existing = (nb.virtualization.virtual_machines.get(name=v["name"], cluster_id=cluster_id)
                    if cluster_id else None)
        obj = sync_item(
            f"vm:{v['name']}", existing, target,
            lambda v=v, t=target: nb.virtualization.virtual_machines.create(name=v["name"], status="active", **t),
            dry_run,
        )
        if obj and v.get("primary_ip"):
            iface = ensure_interface(obj, v.get("interface", "eth0"), is_vm=True)
            assign_primary_ip(obj, iface, is_vm=True, cidr=v["primary_ip"])


def sync_prefixes(data, sites, dry_run):
    print("== Prefixes ==")
    for p in data.get("prefixes", []):
        site = sites.get(p["site"]) if p.get("site") else None
        target = {"description": p.get("description", ""), "site": site.id if site else None}
        existing = nb.ipam.prefixes.get(prefix=p["prefix"])
        sync_item(
            f"prefix:{p['prefix']}", existing, target,
            lambda p=p, t=target: nb.ipam.prefixes.create(prefix=p["prefix"], status="active", **t),
            dry_run,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report what would change, apply nothing")
    args = parser.parse_args()

    if not NETBOX_TOKEN:
        sys.exit("Set NETBOX_TOKEN (Admin > API Tokens in the NetBox UI) before running.")

    global nb
    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

    with open(INVENTORY_FILE) as f:
        data = yaml.safe_load(f)

    sites = sync_sites(data, args.dry_run)
    mfrs = sync_manufacturers(data, args.dry_run)
    roles = sync_device_roles(data, args.dry_run)
    platforms = sync_platforms(data, args.dry_run)
    ctypes = sync_cluster_types(data, args.dry_run)
    dtypes = sync_device_types(data, mfrs, args.dry_run)
    sync_devices(data, dtypes, roles, sites, platforms, args.dry_run)
    clusters = sync_clusters(data, ctypes, sites, args.dry_run)
    sync_vms(data, clusters, roles, platforms, args.dry_run)
    sync_prefixes(data, sites, args.dry_run)

    if args.dry_run:
        print("\nDry run only — nothing was changed. Re-run without --dry-run to apply.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()
