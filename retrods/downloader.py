"""Download an image with progress and verify its SHA-256 checksum."""

from __future__ import annotations

import hashlib
import os
import re
import urllib.request
from typing import Callable

from . import APP_NAME, USER_AGENT

ProgressFn = Callable[[int, int], None]  # (bytes_done, bytes_total or 0)


def default_download_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    path = os.path.join(base, APP_NAME, "downloads")
    os.makedirs(path, exist_ok=True)
    return path


def fetch_sha256(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", "replace")
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not m:
        raise ValueError(f"No SHA-256 hash found in {url}")
    return m.group(1).lower()


def sha256_of(path: str, progress: ProgressFn | None = None, cancel=None) -> str:
    h = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    with open(path, "rb") as fh:
        while True:
            if cancel and cancel():
                raise InterruptedError("cancelled")
            chunk = fh.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


def download(url: str, dest_dir: str | None = None, progress: ProgressFn | None = None,
             cancel=None, expected_sha256: str | None = None) -> str:
    """Download ``url`` into ``dest_dir`` and return the local path.

    If the file already exists and matches ``expected_sha256`` it is reused.
    """
    dest_dir = dest_dir or default_download_dir()
    os.makedirs(dest_dir, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    dest = os.path.join(dest_dir, filename)
    if os.path.isfile(dest) and expected_sha256:
        if sha256_of(dest, progress, cancel) == expected_sha256:
            return dest
        os.remove(dest)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel and cancel():
                out.close()
                os.remove(tmp)
                raise InterruptedError("cancelled")
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, dest)
    if expected_sha256:
        actual = sha256_of(dest, progress, cancel)
        if actual != expected_sha256:
            os.remove(dest)
            raise ValueError(f"Checksum mismatch for {filename}: expected {expected_sha256}, got {actual}")
    return dest
