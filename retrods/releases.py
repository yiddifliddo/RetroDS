"""Discover ROCKNIX images: devices, branches and *versions*.

The official ImageBurner reads ``https://releases.rocknix.org/imageburner/``
which lists one stable and one nightly image per device.  RetroDS uses that
feed to learn the device list and the file-name pattern for each device, then
combines it with the GitHub release history of ``ROCKNIX/distribution`` so the
user can choose any published version, not just the latest.

If the GitHub API is unreachable (rate limit, firewall) the release list is
scraped from ``https://releases.rocknix.org/`` instead.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import USER_AGENT

FEED_URL = "https://releases.rocknix.org/imageburner/"
RELEASES_SITE = "https://releases.rocknix.org/"
GITHUB_RELEASES = "https://api.github.com/repos/ROCKNIX/distribution/releases?per_page=100"
GITHUB_NIGHTLY = "https://api.github.com/repos/ROCKNIX/distribution-nightly/releases?per_page=20"

VERSION_RE = re.compile(r"(\d{8})")


@dataclass(frozen=True)
class Image:
    name: str              # "Anbernic RG CubeXX (DDR4)"
    branch: str            # "stable" / "nightly" / "release"
    version: str           # "20261001" or "nightly-20261003"
    url: str
    sha256_url: str = ""
    post_install: str = ""  # "", "dtb.img", "extlinux", "grubenv"
    dtb: str = ""
    size: int = 0

    @property
    def manufacturer(self) -> str:
        return self.name.split(" ", 1)[0] if " " in self.name else "Other"

    @property
    def device(self) -> str:
        return self.name.split(" ", 1)[1] if " " in self.name else self.name

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def _escape_keep_version(filename: str) -> str:
    parts = VERSION_RE.split(filename)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(r"\d{8}")
        else:
            out.append(re.escape(part))
    return "".join(out)


@dataclass
class Release:
    tag: str
    published: str = ""
    prerelease: bool = False
    nightly: bool = False
    assets: dict[str, tuple[str, int]] = field(default_factory=dict)  # name -> (url, size)
    notes: str = ""

    @property
    def label(self) -> str:
        kind = "nightly" if self.nightly else ("pre-release" if self.prerelease else "release")
        return f"{self.tag}  ({kind}{', ' + self.published[:10] if self.published else ''})"


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_feed(url: str = FEED_URL) -> list[Image]:
    """Parse the ImageBurner XML feed (stable + nightly per device)."""
    root = ET.fromstring(_get(url))
    images: list[Image] = []
    for branch in root:
        for node in branch.findall("image"):
            u = (node.findtext("url") or "").strip()
            if not u:
                continue
            m = VERSION_RE.search(u.rsplit("/", 1)[-1])
            tag_m = re.search(r"/download/([^/]+)/", u)
            version = tag_m.group(1) if tag_m else (m.group(1) if m else "")
            images.append(Image(
                name=(node.findtext("name") or "Unknown").strip(),
                branch=branch.tag.lower(),
                version=version,
                url=u,
                sha256_url=(node.findtext("sha256") or "").strip(),
                post_install=(node.findtext("post_install") or "").strip(),
                dtb=(node.findtext("dtb") or "").strip(),
            ))
    return images


def _parse_github(data: bytes, nightly: bool) -> list[Release]:
    releases = []
    for r in json.loads(data):
        assets = {}
        for a in r.get("assets", []):
            assets[a["name"]] = (a["browser_download_url"], int(a.get("size") or 0))
        releases.append(Release(
            tag=r.get("tag_name", ""),
            published=r.get("published_at", "") or "",
            prerelease=bool(r.get("prerelease")),
            nightly=nightly,
            assets=assets,
            notes=r.get("body", "") or "",
        ))
    return releases


def fetch_releases_github(include_nightly: bool = False) -> list[Release]:
    releases = _parse_github(_get(GITHUB_RELEASES), nightly=False)
    if include_nightly:
        try:
            releases += _parse_github(_get(GITHUB_NIGHTLY), nightly=True)
        except (urllib.error.URLError, ValueError):
            pass
    return releases


def fetch_releases_site() -> list[Release]:
    """Fallback: scrape releases.rocknix.org (no API rate limit)."""
    index = _get(RELEASES_SITE).decode("utf-8", "replace")
    pairs = re.findall(r'<span class="left">\s*(\S+)\s*</span>.*?release_id=(\d+)', index, flags=re.S)
    releases = []
    for tag, rid in pairs:
        page = _get(f"{RELEASES_SITE}?release_id={rid}").decode("utf-8", "replace")
        assets = {}
        for u in re.findall(r'href="(https://github\.com/ROCKNIX/distribution/releases/download/[^"]+)"', page):
            u = html.unescape(u)
            assets[u.rsplit("/", 1)[-1]] = (u, 0)
        releases.append(Release(tag=html.unescape(tag), assets=assets))
    return releases


def fetch_releases(include_nightly: bool = False) -> tuple[list[Release], str]:
    """Return (releases, source) using GitHub first and the website as fallback."""
    try:
        return fetch_releases_github(include_nightly), "github"
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        try:
            return fetch_releases_site(), f"releases.rocknix.org (GitHub API unavailable: {exc})"
        except Exception as exc2:  # noqa: BLE001
            raise RuntimeError(f"Could not fetch the ROCKNIX release list: {exc2}") from exc


def images_for_release(feed: list[Image], release: Release) -> list[Image]:
    """Match each device from the feed to its file in a specific release."""
    seen = set()
    out: list[Image] = []
    for img in feed:
        if img.branch != "stable" or img.name in seen:
            continue
        seen.add(img.name)
        pat = re.compile("^" + _escape_keep_version(img.filename) + "$")
        for asset_name, (url, size) in release.assets.items():
            if pat.match(asset_name):
                sha = release.assets.get(asset_name + ".sha256", ("", 0))[0]
                out.append(Image(name=img.name, branch="nightly" if release.nightly else "release",
                                 version=release.tag, url=url, sha256_url=sha,
                                 post_install=img.post_install, dtb=img.dtb, size=size))
                break
    return out


@dataclass
class Catalog:
    feed: list[Image]
    releases: list[Release]
    source: str = ""

    @property
    def devices(self) -> list[str]:
        names = []
        for img in self.feed:
            if img.branch == "stable" and img.name not in names:
                names.append(img.name)
        return sorted(names)

    def versions(self) -> list[str]:
        out = []
        for img in self.feed:
            tag = f"latest {img.branch} ({img.version})"
            if tag not in out:
                out.append(tag)
        for r in self.releases:
            if r.tag not in out:
                out.append(r.tag)
        return out

    def image(self, device: str, version: str) -> Image | None:
        if version.startswith("latest "):
            branch = version.split()[1]
            for img in self.feed:
                if img.name == device and img.branch == branch:
                    return img
            return None
        for r in self.releases:
            if r.tag == version:
                for img in images_for_release(self.feed, r):
                    if img.name == device:
                        return img
                return None
        return None

    def versions_for_device(self, device: str) -> list[str]:
        out = []
        for img in self.feed:
            if img.name == device:
                out.append(f"latest {img.branch} ({img.version})")
        for r in self.releases:
            if any(i.name == device for i in images_for_release(self.feed, r)):
                out.append(r.tag)
        return out


def load_catalog(include_nightly: bool = False, releases: bool = True) -> Catalog:
    feed = fetch_feed()
    rel, source = ([], "feed only")
    if releases:
        rel, source = fetch_releases(include_nightly)
    return Catalog(feed=feed, releases=rel, source=source)
