"""Write a ROCKNIX ``.img.gz`` to an SD card and apply post-install steps.

Windows: raw access to ``\\.\PHYSICALDRIVEn`` through ctypes (needs
Administrator), locking and dismounting the card's volumes first, exactly as
the official ImageBurner does.  Linux: writes to ``/dev/sdX`` (needs root).

Post-install steps mirror ImageBurner: some devices need ``dtb.img`` copied
or ``extlinux.conf`` / ``grubenv`` edited on the ROCKNIX boot partition.
"""

from __future__ import annotations

import gzip
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .releases import Image

ProgressFn = Callable[[int, int, str], None]   # bytes_done, bytes_total, message
CHUNK = 4 * 1024 * 1024
WIPE_BYTES = 1024 * 1024


class FlashError(Exception):
    pass


@dataclass
class FlashResult:
    bytes_written: int
    seconds: float
    post_install: str = ""


def _gzip_isize(path: str) -> int:
    """Uncompressed size from the gzip footer (modulo 4 GiB)."""
    with open(path, "rb") as fh:
        fh.seek(-4, os.SEEK_END)
        return int.from_bytes(fh.read(4), "little")


def flash(image_path: str, device: str, volumes: list[str] | None = None,
          progress: ProgressFn | None = None, cancel: Callable[[], bool] | None = None,
          image: Image | None = None, post_install: bool = True) -> FlashResult:
    if not os.path.isfile(image_path):
        raise FlashError(f"Image not found: {image_path}")
    compressed_total = os.path.getsize(image_path)
    started = time.time()
    written = 0

    def report(msg: str, pos: int):
        if progress:
            progress(pos, compressed_total, msg)

    if os.name == "nt":
        written = _flash_windows(image_path, device, volumes or [], report, cancel)
    else:
        written = _flash_linux(image_path, device, volumes or [], report, cancel)

    result = FlashResult(bytes_written=written, seconds=time.time() - started)
    if post_install and image is not None and image.post_install and image.dtb:
        report(f"Applying post-install step '{image.post_install}' for {image.dtb}...", compressed_total)
        result.post_install = apply_post_install(image, device)
        report(result.post_install, compressed_total)
    return result


def _stream_image(image_path: str, write: Callable[[bytes], None], sector: int,
                  report, cancel) -> int:
    total_written = 0
    with open(image_path, "rb") as raw, gzip.GzipFile(fileobj=raw) as gz:
        pending = b""
        while True:
            if cancel and cancel():
                raise FlashError("Cancelled")
            data = gz.read(CHUNK)
            if not data:
                break
            buf = pending + data
            aligned = len(buf) - (len(buf) % sector)
            if aligned:
                write(buf[:aligned])
                total_written += aligned
            pending = buf[aligned:]
            report(f"Writing image... {total_written / (1024 * 1024):.0f} MB", raw.tell())
        if pending:
            pad = (sector - len(pending) % sector) % sector
            write(pending + b"\0" * pad)
            total_written += len(pending)
    return total_written


# ---------------------------------------------------------------- Windows ---

def _flash_windows(image_path, device, volumes, report, cancel) -> int:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_NO_BUFFERING, FILE_FLAG_WRITE_THROUGH = 0x20000000, 0x80000000
    FSCTL_LOCK_VOLUME, FSCTL_UNLOCK_VOLUME, FSCTL_DISMOUNT_VOLUME = 0x00090018, 0x0009001C, 0x00090020
    IOCTL_DISK_GET_DRIVE_GEOMETRY, IOCTL_DISK_UPDATE_PROPERTIES = 0x00070000, 0x00070140
    INVALID = ctypes.c_void_p(-1).value

    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

    def err(msg):
        return FlashError(f"{msg} (Windows error {ctypes.get_last_error() or k32.GetLastError()})")

    vol_handles = []
    report("Locking and dismounting volumes...", 0)
    for vol in volumes:
        path = "\\\\.\\" + vol.rstrip("\\/")
        h = k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 3, None, OPEN_EXISTING, 0, None)
        if h == INVALID:
            continue
        ret = wintypes.DWORD()
        k32.DeviceIoControl(h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(ret), None)
        k32.DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(ret), None)
        vol_handles.append(h)

    h = k32.CreateFileW(device, GENERIC_WRITE, 0, None, OPEN_EXISTING,
                        FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH, None)
    if h == INVALID:
        for vh in vol_handles:
            k32.CloseHandle(vh)
        raise err(f"Could not open {device}. Run RetroDS as Administrator and make sure the card is not in use")

    try:
        class DISK_GEOMETRY(ctypes.Structure):
            _fields_ = [("Cylinders", ctypes.c_longlong), ("MediaType", wintypes.DWORD),
                        ("TracksPerCylinder", wintypes.DWORD), ("SectorsPerTrack", wintypes.DWORD),
                        ("BytesPerSector", wintypes.DWORD)]
        geo = DISK_GEOMETRY()
        ret = wintypes.DWORD()
        sector = 512
        if k32.DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_GEOMETRY, None, 0, ctypes.byref(geo),
                               ctypes.sizeof(geo), ctypes.byref(ret), None) and geo.BytesPerSector:
            sector = int(geo.BytesPerSector)

        def write(data: bytes):
            n = wintypes.DWORD()
            buf = ctypes.create_string_buffer(data, len(data))
            if not k32.WriteFile(h, buf, len(data), ctypes.byref(n), None) or n.value != len(data):
                raise err("Write to disk failed")

        report("Wiping old partition table...", 0)
        write(b"\0" * WIPE_BYTES)
        # rewind
        k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
        if not k32.SetFilePointerEx(h, 0, None, 0):
            raise err("Could not rewind disk")
        written = _stream_image(image_path, write, sector, report, cancel)
        k32.FlushFileBuffers(h)
    finally:
        k32.CloseHandle(h)
        for vh in vol_handles:
            ret = wintypes.DWORD()
            k32.DeviceIoControl(vh, FSCTL_UNLOCK_VOLUME, None, 0, None, 0, ctypes.byref(ret), None)
            k32.CloseHandle(vh)
        try:
            h2 = k32.CreateFileW(device, GENERIC_READ | GENERIC_WRITE, 3, None, OPEN_EXISTING, 0, None)
            if h2 != INVALID:
                ret = wintypes.DWORD()
                k32.DeviceIoControl(h2, IOCTL_DISK_UPDATE_PROPERTIES, None, 0, None, 0, ctypes.byref(ret), None)
                k32.CloseHandle(h2)
        except Exception:  # noqa: BLE001
            pass
    return written


# ------------------------------------------------------------------ Linux ---

def _flash_linux(image_path, device, volumes, report, cancel) -> int:
    if not os.path.exists(device):
        raise FlashError(f"Device not found: {device}")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise FlashError("Writing to a block device needs root. Run RetroDS with sudo.")
    report("Unmounting partitions...", 0)
    for mp in volumes:
        subprocess.run(["umount", mp], capture_output=True)
    try:
        out = subprocess.run(["lsblk", "-n", "-o", "PATH,MOUNTPOINT", device], capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip():
                subprocess.run(["umount", parts[0]], capture_output=True)
    except OSError:
        pass
    sector = 512
    fd = os.open(device, os.O_WRONLY)
    try:
        def write(data: bytes):
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        report("Wiping old partition table...", 0)
        write(b"\0" * WIPE_BYTES)
        os.lseek(fd, 0, os.SEEK_SET)
        written = _stream_image(image_path, write, sector, report, cancel)
        os.fsync(fd)
    finally:
        os.close(fd)
    subprocess.run(["partprobe", device], capture_output=True)
    subprocess.run(["udevadm", "settle"], capture_output=True)
    return written


# ----------------------------------------------------------- post install ---

def _find_rocknix_volume(device: str, timeout: float = 30.0) -> str | None:
    """Return a path where the ROCKNIX boot partition is mounted."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.name == "nt":
            import ctypes
            buf = ctypes.create_unicode_buffer(261)
            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                try:
                    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
                except Exception:  # noqa: BLE001
                    ok = 0
                if ok and buf.value.upper() == "ROCKNIX":
                    return root
        else:
            by_label = "/dev/disk/by-label/ROCKNIX"
            if os.path.exists(by_label):
                real = os.path.realpath(by_label)
                if real.startswith(device):
                    out = subprocess.run(["findmnt", "-n", "-o", "TARGET", real], capture_output=True, text=True).stdout.strip()
                    if out:
                        return out
                    mnt = "/tmp/retrods-rocknix-boot"
                    os.makedirs(mnt, exist_ok=True)
                    if subprocess.run(["mount", real, mnt], capture_output=True).returncode == 0:
                        return mnt
        time.sleep(2)
    return None


def apply_post_install(image: Image, device: str) -> str:
    root = _find_rocknix_volume(device)
    if not root:
        return ("Post-install step skipped: the ROCKNIX boot partition did not appear. "
                "Remove and re-insert the card, then run 'Post-install only' from the Install tab.")
    try:
        action = image.post_install.lower()
        if action == "dtb.img":
            src = os.path.join(root, "device_trees", image.dtb + ".dtb")
            dst = os.path.join(root, "dtb.img")
            if not os.path.isfile(src):
                return f"Post-install: {src} not found; the image may already be device-specific."
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                fo.write(fi.read())
            return f"Post-install: copied {image.dtb}.dtb to dtb.img"
        if action == "extlinux":
            conf = os.path.join(root, "extlinux", "extlinux.conf")
            if not os.path.isfile(conf):
                return f"Post-install: {conf} not found"
            with open(conf, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            for i, line in enumerate(lines):
                if line.strip().upper().startswith("FDT"):
                    indent = line[: len(line) - len(line.lstrip())]
                    lines[i] = f"{indent}FDT /device_trees/{image.dtb}.dtb"
                    break
            with open(conf, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
            return f"Post-install: extlinux.conf now boots {image.dtb}.dtb"
        if action == "grubenv":
            path = os.path.join(root, "boot", "grub", "grubenv")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            header = ("# GRUB Environment Block\n"
                      "# WARNING: Do not edit this file by tools other than grub-editenv!!!\n"
                      f"saved_entry={image.dtb}\n").encode("utf-8")
            with open(path, "wb") as fh:
                fh.write(header + b"#" * (1024 - len(header)))
            return f"Post-install: grubenv set to {image.dtb}"
        return f"Post-install: unknown action '{image.post_install}' (nothing done)"
    finally:
        if os.name != "nt" and root == "/tmp/retrods-rocknix-boot":
            subprocess.run(["umount", root], capture_output=True)
