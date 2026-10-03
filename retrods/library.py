"""Scan a Batocera / Knulli library (local folder or network share).

The scanner is read-only.  It finds the ``roms`` folder, walks each system
folder, parses ``gamelist.xml`` and counts games, ROM files and scraped media
so the user can review what will be migrated before anything is copied.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .systems import MEDIA_DIRS, PATH_TAGS, Mapping, build_table, map_system

IGNORED_FILES = {"thumbs.db", ".ds_store", "desktop.ini", ".gitkeep", "_info.txt"}
IGNORED_DIRS = {"@eadir", ".thumbnails", "system volume information", "$recycle.bin"}
# Folders at the roms/ level that are not game systems.
NON_SYSTEM_DIRS = {"bios", "cheats", "decorations", "music", "saves", "screenshots",
                   "system", "themes", "splash", "kodi", "extractions", "bezels"}


@dataclass
class GameEntry:
    path: str
    name: str
    media: dict[str, str] = field(default_factory=dict)  # tag -> relative path


@dataclass
class SystemScan:
    source_name: str
    source_dir: str
    mapping: Mapping
    has_gamelist: bool = False
    gamelist_games: int = 0
    gamelist_with_media: int = 0
    rom_files: int = 0
    media_files: int = 0
    total_files: int = 0
    total_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def target_name(self) -> str | None:
        return self.mapping.target

    @property
    def status(self) -> str:
        return self.mapping.status


@dataclass
class LibraryScan:
    root: str            # the folder the user entered
    roms_dir: str        # the resolved roms folder
    bios_dir: str | None
    flavour: str         # "batocera", "knulli" or "unknown"
    systems: list[SystemScan] = field(default_factory=list)

    @property
    def supported(self) -> list[SystemScan]:
        return [s for s in self.systems if s.mapping.supported]

    @property
    def unsupported(self) -> list[SystemScan]:
        return [s for s in self.systems if not s.mapping.supported]

    @property
    def total_bytes(self) -> int:
        return sum(s.total_bytes for s in self.systems)


def find_roms_dir(root: str) -> str:
    """Accept the share root, the userdata folder or the roms folder itself."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Folder not found: {root}")
    for candidate in (root, os.path.join(root, "roms"), os.path.join(root, "share", "roms"),
                      os.path.join(root, "userdata", "roms")):
        if os.path.isdir(candidate) and _looks_like_roms_dir(candidate):
            return candidate
    raise FileNotFoundError(
        f"Could not find a 'roms' folder with system subfolders under {root}. "
        "Point RetroDS at the Batocera/Knulli share root (the folder containing "
        "'roms', 'bios', 'saves'...) or directly at the 'roms' folder."
    )


def _looks_like_roms_dir(path: str) -> bool:
    try:
        names = {n.lower() for n in os.listdir(path) if os.path.isdir(os.path.join(path, n))}
    except OSError:
        return False
    known = {"snes", "nes", "megadrive", "psx", "gba", "gb", "gbc", "mame", "fbneo",
             "n64", "psp", "dreamcast", "arcade", "neogeo", "pcengine", "mastersystem"}
    return len(names & known) >= 1


def detect_flavour(root: str, roms_dir: str) -> str:
    share = os.path.dirname(roms_dir)
    for base in (root, share):
        sysdir = os.path.join(base, "system")
        if os.path.isdir(sysdir):
            for marker, flavour in (("knulli.conf", "knulli"), ("batocera.conf", "batocera")):
                if os.path.isfile(os.path.join(sysdir, marker)):
                    return flavour
    return "unknown"


def parse_gamelist(path: str) -> list[GameEntry]:
    """Parse a gamelist.xml into GameEntry objects (tolerant of odd files)."""
    tree = ET.parse(path)
    root = tree.getroot()
    entries: list[GameEntry] = []
    for node in root:
        if node.tag not in ("game", "folder"):
            continue
        p = (node.findtext("path") or "").strip()
        name = (node.findtext("name") or "").strip()
        media = {}
        for tag in PATH_TAGS:
            if tag == "path":
                continue
            val = (node.findtext(tag) or "").strip()
            if val:
                media[tag] = val
        entries.append(GameEntry(p, name, media))
    return entries


def scan_system(source_dir: str, mapping: Mapping, progress=None) -> SystemScan:
    scan = SystemScan(os.path.basename(source_dir), source_dir, mapping)
    gamelist = os.path.join(source_dir, "gamelist.xml")
    if os.path.isfile(gamelist):
        scan.has_gamelist = True
        try:
            entries = parse_gamelist(gamelist)
            scan.gamelist_games = sum(1 for e in entries if e.path)
            scan.gamelist_with_media = sum(1 for e in entries if e.media)
        except ET.ParseError as exc:
            scan.errors.append(f"gamelist.xml could not be parsed: {exc}")
    for dirpath, dirnames, filenames in os.walk(source_dir):
        dirnames[:] = [d for d in dirnames if d.lower() not in IGNORED_DIRS]
        rel = os.path.relpath(dirpath, source_dir)
        top = rel.split(os.sep)[0].lower() if rel != "." else ""
        in_media = top in MEDIA_DIRS
        for fn in filenames:
            if fn.lower() in IGNORED_FILES:
                continue
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                scan.errors.append(f"{full}: {exc}")
                continue
            scan.total_files += 1
            scan.total_bytes += size
            if fn.lower() == "gamelist.xml" and rel == ".":
                continue
            if in_media:
                scan.media_files += 1
            else:
                scan.rom_files += 1
        if progress:
            progress(scan)
    return scan


def scan_library(root: str, overrides: dict[str, str | None] | None = None,
                 progress=None) -> LibraryScan:
    """Scan a whole Batocera/Knulli library.

    ``progress`` is called with (system_name, index, total) before each system.
    """
    roms_dir = find_roms_dir(root)
    share = os.path.dirname(roms_dir)
    bios_dir = os.path.join(share, "bios")
    result = LibraryScan(root=root, roms_dir=roms_dir,
                         bios_dir=bios_dir if os.path.isdir(bios_dir) else None,
                         flavour=detect_flavour(root, roms_dir))
    table = build_table(overrides)
    names = sorted(n for n in os.listdir(roms_dir)
                   if os.path.isdir(os.path.join(roms_dir, n))
                   and not n.startswith(".")
                   and n.lower() not in IGNORED_DIRS
                   and n.lower() not in NON_SYSTEM_DIRS)
    for idx, name in enumerate(names):
        if progress:
            progress(name, idx, len(names))
        mapping = map_system(name, table)
        result.systems.append(scan_system(os.path.join(roms_dir, name), mapping))
    return result


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TB"


def format_report(scan: LibraryScan) -> str:
    lines = [
        f"Library: {scan.roms_dir}  (flavour: {scan.flavour})",
        f"BIOS folder: {scan.bios_dir or 'not found'}",
        "",
        f"{'Source':<18}{'ROCKNIX':<18}{'Status':<12}{'Games':>7}{'Media':>7}{'Files':>8}{'Size':>11}",
        "-" * 81,
    ]
    for s in scan.systems:
        lines.append(
            f"{s.source_name:<18}{(s.target_name or '-'):<18}{s.status:<12}"
            f"{s.gamelist_games:>7}{s.media_files:>7}{s.total_files:>8}{human_size(s.total_bytes):>11}"
        )
        for err in s.errors:
            lines.append(f"    ! {err}")
    lines.append("-" * 81)
    lines.append(f"{len(scan.supported)} systems will be migrated, "
                 f"{len(scan.unsupported)} skipped (unsupported/unknown), "
                 f"{human_size(sum(s.total_bytes for s in scan.supported))} to copy.")
    return "\n".join(lines)
