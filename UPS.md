# Proxmox + TrueNAS SCALE + CyberPower UPS (NUT) Setup

## Overview

This document describes how I configured a CyberPower CP1500PFCLCDa UPS for a Proxmox VE host running a virtualized TrueNAS SCALE instance.

### Hardware

* Proxmox VE 9.2
* TrueNAS SCALE running as a VM
* CyberPower CP1500PFCLCDa UPS
* USB connection from UPS directly to the Proxmox host

## Design

The UPS is connected to the **Proxmox host**, not directly to the TrueNAS VM.

Reasons:

* The hypervisor should know when utility power is lost.
* Proxmox can gracefully shut down virtual machines before powering itself off.
* TrueNAS receives UPS status over the network instead of owning the USB device.
* Prevents storage issues caused by the VM shutting down unexpectedly.

Architecture:

```text
CyberPower UPS (USB)
        │
        ▼
Proxmox Host
   ├── NUT Driver (usbhid-ups)
   ├── upsd
   ├── upsmon (Primary)
   │
   └──────────────► TrueNAS SCALE
                     UPS Mode: Slave
```

---

## Install NUT

```bash
apt update
apt install nut
```

---

## Verify UPS Detection

Confirm the UPS appears over USB:

```bash
lsusb
```

Expected:

```text
0764:0601 Cyber Power System, Inc.
```

---

## Configure UPS Driver

`/etc/nut/ups.conf`

```ini
maxretry = 3

[cyberpower]
    driver = usbhid-ups
    port = auto
    desc = "CyberPower CP1500PFCLCDa"
```

---

## Fix USB Permissions

Reload udev rules:

```bash
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb
```

Verify:

```bash
ls -l /dev/bus/usb/001/002
```

Expected owner/group:

```text
root nut
```

---

## Test Driver

```bash
upsdrvctl start
```

Verify:

```bash
upsc cyberpower
```

Example output:

* battery charge
* runtime
* load
* input voltage
* `ups.status: OL`

---

## Configure NUT Server

`/etc/nut/nut.conf`

```ini
MODE=standalone
```

---

## Configure Users

`/etc/nut/upsd.users`

```ini
[monuser]
    password = CHANGE_ME
    upsmon primary
```

---

## Configure upsmon

`/etc/nut/upsmon.conf`

```ini
MONITOR cyberpower@localhost 1 monuser CHANGE_ME primary
```

---

## Start Services

```bash
systemctl enable nut-server
systemctl enable nut-monitor

systemctl restart nut-server
systemctl restart nut-monitor
```

Verify:

```bash
systemctl status nut-server
systemctl status nut-monitor
```

---

## Verify Operation

```bash
upsc cyberpower
```

Expected:

```text
ups.status: OL
```

---

## Configure TrueNAS SCALE

UPS Mode:

* Slave

Remote Host:

* Proxmox IP Address

UPS Name:

* cyberpower

Username:

* monuser

Password:

* Same password configured in `upsd.users`

---

## Useful Commands

UPS status:

```bash
upsc cyberpower
```

Driver status:

```bash
systemctl status nut-driver
```

Server status:

```bash
systemctl status nut-server
```

Monitor status:

```bash
systemctl status nut-monitor
```

USB devices:

```bash
lsusb
```

---

## Lessons Learned

* Do **not** pass the UPS USB device through to the TrueNAS VM.
* Let Proxmox own the UPS.
* Configure TrueNAS as a NUT client.
* Verify USB permissions if `usbhid-ups` reports permission errors.
* If `upsc` reports "Connection refused," ensure `upsd` is running.
* If `upsmon` reports `ERR ACCESS-DENIED`, verify that `upsd.users` and `upsmon.conf` use matching usernames and passwords.

