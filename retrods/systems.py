"""System folder name mapping between Batocera / Knulli and ROCKNIX.

Batocera and Knulli keep games in ``/userdata/roms/<system>/``.
ROCKNIX keeps games in ``/storage/roms/<system>/``.

The EmulationStation builds on all three distributions share the same
gamelist.xml format and media tags, so a migration is mostly a copy of each
system folder.  The one real difference is that some system folder names
differ.  This module holds that mapping.

A user can override or extend the table by placing a ``systems_map.json``
file next to the executable (or in the working directory) with entries of
the form ``{"batocera_name": "rocknix_name"}``.  Mapping a system to an
empty string or ``null`` marks it as unsupported.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

# ROCKNIX system folders that exist under /storage/roms, taken from the ROCKNIX
# wiki (systems section) and the ROCKNIX EmulationStation configuration.
ROCKNIX_SYSTEMS = {
    "3do", "3ds", "amiga", "amigacd32", "amstradcpc", "arduboy",
    "atari2600", "atari5200", "atari7800", "atari800", "atarijaguar",
    "atarilynx", "atarist", "atomiswave", "build", "c128", "c16", "c64",
    "cdi", "channelf", "chip-8", "coleco", "cps1", "cps2", "cps3", "daphne",
    "doom", "dreamcast", "easyrpg", "famicom", "fbneo", "fds", "gameandwatch",
    "gamecube", "gamegear", "gb", "gba", "gbc", "genesis", "gp32", "heroic",
    "idtech", "intellivision", "j2me", "mac", "mame", "mastersystem",
    "megaduck", "moonlight", "moto", "mplayer", "msx", "music", "n64",
    "naomi", "nds", "neocd", "neogeo", "nes", "ngp", "ngpc", "odyssey",
    "openbor", "palm", "pc", "pc88", "pc98", "pcengine", "pcenginecd",
    "pcfx", "pet", "pico-8", "pokemini", "ports", "ps2", "ps3", "psp",
    "psvita", "psx", "satellaview", "saturn", "scummvm", "scv", "sega32x",
    "segacd", "sfc", "sg-1000", "sgfx", "snes", "snesmsu1", "steam",
    "sufami", "supervision", "tg16", "tg16cd", "tic-80", "uzebox", "vectrex",
    "vic20", "videopac", "vircon32", "virtualboy", "wii", "wiiu",
    "wonderswan", "wonderswancolor", "x1", "x68000", "xbox", "zmachine",
    "zx81", "zxspectrum",
}

# Batocera / Knulli folder name -> ROCKNIX folder name.
# Only names that DIFFER (or merge) are listed; identical names map 1:1
# automatically when they exist in ROCKNIX_SYSTEMS.
# ``None`` means "ROCKNIX has no equivalent system".
RENAMES: dict[str, str | None] = {
    # Sega
    "megadrive": "genesis",
    "megacd": "segacd",
    "sg1000": "sg-1000",
    "pico": None,
    "multivision": None,
    "sc3000": None,
    "segaai": None,
    "beena": None,
    "naomi2": None,
    "model2": None,
    "model3": None,
    "hikaru": None,
    "systemsp": None,
    "triforce": None,
    "chihiro": None,
    "lindbergh": None,
    # Atari
    "lynx": "atarilynx",
    "jaguar": "atarijaguar",
    "jaguarcd": None,
    "xegs": "atari800",
    # Bandai / SNK / NEC
    "wswan": "wonderswan",
    "wswanc": "wonderswancolor",
    "supergrafx": "sgfx",
    "neogeocd": "neocd",
    # Nintendo
    "snes-msu1": "snesmsu1",
    "sgb": None,
    "gb2players": None,
    "gbc2players": None,
    "n64dd": None,
    "switch": None,
    # Commodore / Amiga
    "amiga500": "amiga",
    "amiga1200": "amiga",
    "amiga": "amiga",
    "amigacdtv": None,
    "c20": "vic20",
    "cplus4": "c16",
    # MSX family
    "msx1": "msx",
    "msx2": "msx",
    "msxturbor": "msx",
    "msx": "msx",
    # Philips / Magnavox
    "odyssey2": "odyssey",
    "o2em": "odyssey",
    "videopacplus": "videopac",
    # Apple
    "macintosh": "mac",
    "apple2": None,
    "apple2gs": None,
    # Fantasy consoles
    "pico8": "pico-8",
    "tic80": "tic-80",
    "lowresnx": None,
    "wasm4": None,
    "pyxel": None,
    # Ports and engines
    "dos": "pc",
    "prboom": "doom",
    "gzdoom": "doom",
    "uzdoom": "doom",
    "quake": "idtech",
    "tyrquake": "idtech",
    "quake2": "idtech",
    "vitaquake2": "idtech",
    "quake3": "idtech",
    "eduke32": "build",
    "fury": "build",
    "raze": "build",
    "vgmplay": "music",
    "colecovision": "coleco",
    "lcdgames": None,
    "tvgames": None,
    "plugnplay": None,
    "gx4000": None,
    "pcw": None,
    "bbc": None,
    "bbcmicro": None,
    "flash": None,
    "windows": None,
    "windows_installers": None,
    "flatpak": None,
    "library": None,
    "imageviewer": None,
    "recordings": None,
    "mugen": None,
    "ikemen": None,
    "tools": None,
    "emulators": None,
    "mpv": None,
    "xbox360": None,
    "ps4": None,
    "xash3d_fwgs": None,
    "cavestory": None,
    "mrboom": None,
    "cannonball": None,
    "sdlpop": None,
    "lutro": None,
    "fpinball": None,
    "vpinball": None,
}

# Subfolders inside a system folder that hold scraped media rather than games.
MEDIA_DIRS = {
    "images", "videos", "manuals", "marquees", "screenshots", "thumbnails",
    "wheel", "wheels", "boxart", "boxback", "mix", "magazines", "maps",
    "fanart", "titleshots", "cartridge", "cartridges", "media",
    "downloaded_images", "downloaded_videos", "downloaded_media",
}

# gamelist.xml tags whose value is a file path.
PATH_TAGS = (
    "path", "image", "thumbnail", "video", "marquee", "fanart", "titleshot",
    "manual", "magazine", "map", "cartridge", "boxart", "boxback", "wheel",
    "mix",
)


@dataclass(frozen=True)
class Mapping:
    source: str
    target: str | None
    status: str  # "same", "renamed", "merged", "unsupported", "unknown"

    @property
    def supported(self) -> bool:
        return self.target is not None


def _override_paths() -> list[str]:
    paths = []
    if getattr(sys, "frozen", False):
        paths.append(os.path.join(os.path.dirname(sys.executable), "systems_map.json"))
    paths.append(os.path.join(os.getcwd(), "systems_map.json"))
    return paths


def load_overrides(explicit: str | None = None) -> dict[str, str | None]:
    """Load user overrides from systems_map.json if present."""
    candidates = [explicit] if explicit else _override_paths()
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError(f"{path}: expected a JSON object of source->target names")
            return {str(k).strip().lower(): (str(v).strip().lower() or None) if v else None
                    for k, v in data.items()}
    return {}


def build_table(overrides: dict[str, str | None] | None = None) -> dict[str, str | None]:
    table = dict(RENAMES)
    if overrides:
        table.update(overrides)
    return table


def map_system(name: str, table: dict[str, str | None] | None = None) -> Mapping:
    """Map a Batocera/Knulli system folder name to its ROCKNIX folder name."""
    table = table if table is not None else build_table()
    key = name.strip().lower()
    if key in table:
        target = table[key]
        if target is None:
            return Mapping(key, None, "unsupported")
        merged = sum(1 for k, v in table.items() if v == target) > 1 or (
            target in table and target != key
        )
        if target == key:
            return Mapping(key, target, "same")
        return Mapping(key, target, "merged" if merged else "renamed")
    if key in ROCKNIX_SYSTEMS:
        return Mapping(key, key, "same")
    return Mapping(key, None, "unknown")
