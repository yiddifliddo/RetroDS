"""List candidate SD cards / USB drives on Windows and Linux."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class Disk:
    device: str                 # \\.\PHYSICALDRIVE2  or  /dev/sdb
    model: str
    size: int
    removable: bool
    volumes: list[str] = field(default_factory=list)   # "E:\\" or mount points
    system: bool = False

    @property
    def label(self) -> str:
        from .library import human_size
        vols = ", ".join(self.volumes) if self.volumes else "no volumes"
        flag = "  [SYSTEM DISK]" if self.system else ("" if self.removable else "  [fixed disk]")
        return f"{self.device}  {self.model}  {human_size(self.size)}  ({vols}){flag}"


def list_disks(show_all: bool = False) -> list[Disk]:
    if os.name == "nt":
        disks = _list_windows()
    else:
        disks = _list_linux()
    if not show_all:
        disks = [d for d in disks if d.removable and not d.system]
    return disks


# ---------------------------------------------------------------- Windows ---

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$sysDrive = ($env:SystemDrive)
$out = @()
foreach ($d in Get-CimInstance Win32_DiskDrive) {
  $vols = @()
  $isSys = $false
  $parts = Get-CimAssociatedInstance -InputObject $d -ResultClassName Win32_DiskPartition
  foreach ($p in $parts) {
    $lds = Get-CimAssociatedInstance -InputObject $p -ResultClassName Win32_LogicalDisk
    foreach ($ld in $lds) {
      $vols += ($ld.DeviceID + '\')
      if ($ld.DeviceID -eq $sysDrive) { $isSys = $true }
    }
  }
  $out += [pscustomobject]@{
    DeviceID = $d.DeviceID; Model = $d.Model; Size = [int64]$d.Size;
    InterfaceType = $d.InterfaceType; MediaType = $d.MediaType;
    Volumes = $vols; System = $isSys
  }
}
$out | ConvertTo-Json -Compress
"""


def _list_windows() -> list[Disk]:
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not query disks via PowerShell: {exc}") from exc
    text = res.stdout.strip()
    if not text:
        raise RuntimeError(f"PowerShell returned no disk information. {res.stderr.strip()}")
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    disks = []
    for d in data:
        media = (d.get("MediaType") or "").lower()
        iface = (d.get("InterfaceType") or "").upper()
        removable = iface == "USB" or "removable" in media or "external" in media
        vols = d.get("Volumes") or []
        if isinstance(vols, str):
            vols = [vols]
        disks.append(Disk(device=d.get("DeviceID", ""), model=(d.get("Model") or "Unknown").strip(),
                          size=int(d.get("Size") or 0), removable=removable, volumes=list(vols),
                          system=bool(d.get("System"))))
    return disks


# ------------------------------------------------------------------ Linux ---

def _list_linux() -> list[Disk]:
    try:
        res = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TRAN,RM,TYPE,MOUNTPOINT,HOTPLUG"],
                             capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"lsblk failed: {exc}") from exc
    data = json.loads(res.stdout)
    disks = []
    root_dev = _linux_root_disk()
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        path = dev.get("path") or f"/dev/{dev.get('name')}"
        mounts = []
        for child in dev.get("children", []) or []:
            if child.get("mountpoint"):
                mounts.append(child["mountpoint"])
        tran = (dev.get("tran") or "").lower()
        removable = bool(dev.get("rm")) or bool(dev.get("hotplug")) or tran in ("usb", "mmc") \
            or dev.get("name", "").startswith("mmcblk")
        system = (root_dev is not None and path == root_dev) or "/" in mounts
        disks.append(Disk(device=path, model=(dev.get("model") or "Unknown").strip(),
                          size=int(dev.get("size") or 0), removable=removable, volumes=mounts,
                          system=system))
    return disks


def _linux_root_disk() -> str | None:
    try:
        res = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True, timeout=10)
        src = res.stdout.strip()
        if not src.startswith("/dev/"):
            return None
        res = subprocess.run(["lsblk", "-n", "-o", "PKNAME", src], capture_output=True, text=True, timeout=10)
        pk = res.stdout.strip().splitlines()[0].strip() if res.stdout.strip() else ""
        return f"/dev/{pk}" if pk else src
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def is_admin() -> bool:
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def relaunch_as_admin() -> bool:
    """Windows only: re-launch the current program elevated. Returns True if launched."""
    if os.name != "nt":
        return False
    import ctypes
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32
