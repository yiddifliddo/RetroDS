"""Copy a scanned Batocera / Knulli library into a ROCKNIX roms folder.

Responsibilities:

* build a copy plan from a :class:`LibraryScan` (so it can be shown and
  dry-run before anything is written);
* copy ROM files and scraped media, skipping files that already exist with
  the same size (safe to re-run / resume);
* rewrite ``gamelist.xml`` so absolute Batocera paths become relative, and
  merge gamelists when several source systems land in one ROCKNIX folder
  (for example amiga500 + amiga1200 -> amiga);
* optionally copy the BIOS folder to ``roms/bios``.
"""

from __future__ import annotations

import os
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

from .library import IGNORED_DIRS, IGNORED_FILES, LibraryScan, SystemScan, human_size
from .systems import MEDIA_DIRS, PATH_TAGS, build_table, map_system

ProgressFn = Callable[["CopyProgress"], None]


@dataclass
class FileJob:
    source: str
    target: str
    size: int


@dataclass
class SystemJob:
    scan: SystemScan
    target_dir: str
    files: list[FileJob] = field(default_factory=list)
    gamelist_source: str | None = None

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class Plan:
    scan: LibraryScan
    target_roms: str
    systems: list[SystemJob] = field(default_factory=list)
    bios_files: list[FileJob] = field(default_factory=list)
    skipped_existing: int = 0
    skipped_bytes: int = 0

    @property
    def total_files(self) -> int:
        return sum(len(s.files) for s in self.systems) + len(self.bios_files)

    @property
    def total_bytes(self) -> int:
        return sum(s.total_bytes for s in self.systems) + sum(f.size for f in self.bios_files)


@dataclass
class CopyProgress:
    phase: str                 # "planning", "copying", "gamelist", "done"
    system: str = ""
    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    message: str = ""


@dataclass
class Options:
    dry_run: bool = False
    overwrite: bool = False
    copy_bios: bool = True
    include_unknown: bool = False       # copy "unknown" systems under their own name
    include_media: bool = True
    include_roms: bool = True
    verify_sizes: bool = True


class MigrationCancelled(Exception):
    pass


def find_target_roms(target: str) -> str:
    """Accept the ROCKNIX share root (games-roms) or the roms folder itself."""
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise FileNotFoundError(
            f"ROCKNIX destination not found: {target}\n"
            "Boot the device, enable Samba in Network Settings and enter the share path, "
            "for example \\\\192.168.1.50\\games-roms (Windows) or /mnt/rocknix/games-roms."
        )
    if os.path.basename(target).lower() == "roms":
        return target
    sub = os.path.join(target, "roms")
    if os.path.isdir(sub):
        return sub
    # The ROCKNIX "games-roms" Samba share *is* the roms folder (system folders
    # sit directly inside it), so with no 'roms' subfolder the target itself is
    # the destination.
    return target


def _same_file(src: str, dst: str, size: int) -> bool:
    try:
        st = os.stat(dst)
    except OSError:
        return False
    return st.st_size == size


def build_plan(scan: LibraryScan, target: str, options: Options | None = None,
               overrides: dict[str, str | None] | None = None,
               progress: ProgressFn | None = None) -> Plan:
    options = options or Options()
    target_roms = find_target_roms(target)
    plan = Plan(scan=scan, target_roms=target_roms)
    table = build_table(overrides)

    for sys_scan in scan.systems:
        mapping = map_system(sys_scan.source_name, table)
        if mapping.target is None:
            if mapping.status == "unknown" and options.include_unknown:
                target_name = mapping.source
            else:
                continue
        else:
            target_name = mapping.target
        target_dir = os.path.join(target_roms, target_name)
        job = SystemJob(scan=sys_scan, target_dir=target_dir)
        if progress:
            progress(CopyProgress("planning", system=sys_scan.source_name))
        src_root = sys_scan.source_dir
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames if d.lower() not in IGNORED_DIRS]
            rel_dir = os.path.relpath(dirpath, src_root)
            top = rel_dir.split(os.sep)[0].lower() if rel_dir != "." else ""
            is_media = top in MEDIA_DIRS
            if is_media and not options.include_media:
                continue
            if not is_media and not options.include_roms and rel_dir != ".":
                continue
            for fn in filenames:
                if fn.lower() in IGNORED_FILES:
                    continue
                src = os.path.join(dirpath, fn)
                if rel_dir == "." and fn.lower() == "gamelist.xml":
                    job.gamelist_source = src
                    continue
                if rel_dir == "." and not options.include_roms and not is_media:
                    continue
                try:
                    size = os.path.getsize(src)
                except OSError:
                    continue
                dst = os.path.join(target_dir, rel_dir, fn) if rel_dir != "." else os.path.join(target_dir, fn)
                if not options.overwrite and options.verify_sizes and _same_file(src, dst, size):
                    plan.skipped_existing += 1
                    plan.skipped_bytes += size
                    continue
                job.files.append(FileJob(src, dst, size))
        plan.systems.append(job)

    if options.copy_bios and scan.bios_dir and os.path.isdir(scan.bios_dir):
        bios_target = os.path.join(target_roms, "bios")
        for dirpath, dirnames, filenames in os.walk(scan.bios_dir):
            dirnames[:] = [d for d in dirnames if d.lower() not in IGNORED_DIRS]
            rel_dir = os.path.relpath(dirpath, scan.bios_dir)
            for fn in filenames:
                if fn.lower() in IGNORED_FILES or fn.lower() == "readme.txt":
                    continue
                src = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(src)
                except OSError:
                    continue
                dst = os.path.join(bios_target, rel_dir, fn) if rel_dir != "." else os.path.join(bios_target, fn)
                if not options.overwrite and _same_file(src, dst, size):
                    plan.skipped_existing += 1
                    plan.skipped_bytes += size
                    continue
                plan.bios_files.append(FileJob(src, dst, size))
    return plan


# --------------------------------------------------------------------------
# gamelist handling
# --------------------------------------------------------------------------

SOURCE_ROOTS = ("/userdata/roms/", "/storage/roms/", "/recalbox/share/roms/")


def rewrite_path(value: str, source_system: str, table: dict[str, str | None]) -> str:
    """Make a gamelist path relative to the system folder where possible."""
    v = value.strip()
    if not v:
        return v
    norm = v.replace("\\", "/")
    for root in SOURCE_ROOTS:
        if norm.startswith(root):
            rest = norm[len(root):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                sys_name, tail = parts
                if sys_name.lower() == source_system.lower():
                    return "./" + tail
                mapped = map_system(sys_name, table)
                if mapped.target:
                    return "/storage/roms/" + mapped.target + "/" + tail
            return "/storage/roms/" + rest
    if norm.startswith("./") or norm.startswith("/") or norm.startswith("~/"):
        return v
    # bare relative path -> make it explicit
    return "./" + norm


def _entry_key(node: ET.Element) -> str:
    p = (node.findtext("path") or "").strip().replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p.lower()


def rewrite_gamelist(source_xml: str, source_system: str, table: dict[str, str | None]) -> ET.ElementTree:
    tree = ET.parse(source_xml)
    root = tree.getroot()
    for node in root:
        if node.tag not in ("game", "folder"):
            continue
        for tag in PATH_TAGS:
            el = node.find(tag)
            if el is not None and el.text:
                el.text = rewrite_path(el.text, source_system, table)
    return tree


def merge_gamelists(existing_xml: str, incoming: ET.ElementTree) -> ET.ElementTree:
    """Merge ``incoming`` entries into an existing gamelist (incoming wins)."""
    try:
        base = ET.parse(existing_xml)
    except ET.ParseError:
        return incoming
    base_root = base.getroot()
    index = {_entry_key(n): n for n in base_root if n.tag in ("game", "folder")}
    for node in incoming.getroot():
        if node.tag not in ("game", "folder"):
            continue
        key = _entry_key(node)
        if key in index:
            base_root.remove(index[key])
        base_root.append(node)
    return base


def write_gamelist(tree: ET.ElementTree, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        ET.indent(tree, space="\t")  # Python 3.9+
    except AttributeError:
        pass
    tmp = path + ".retrods.tmp"
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def _copy_file(job: FileJob, buf_size: int = 4 * 1024 * 1024, on_bytes=None, cancel=None) -> None:
    os.makedirs(os.path.dirname(job.target), exist_ok=True)
    tmp = job.target + ".retrods.part"
    with open(job.source, "rb") as src, open(tmp, "wb") as dst:
        while True:
            if cancel and cancel():
                dst.close()
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise MigrationCancelled()
            chunk = src.read(buf_size)
            if not chunk:
                break
            dst.write(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    try:
        shutil.copystat(job.source, tmp)
    except OSError:
        pass
    os.replace(tmp, job.target)


def execute_plan(plan: Plan, options: Options | None = None,
                 overrides: dict[str, str | None] | None = None,
                 progress: ProgressFn | None = None,
                 cancel: Callable[[], bool] | None = None,
                 log: Callable[[str], None] | None = None) -> CopyProgress:
    options = options or Options()
    table = build_table(overrides)
    log = log or (lambda s: None)
    state = CopyProgress("copying", files_total=plan.total_files, bytes_total=plan.total_bytes)

    def report():
        if progress:
            progress(state)

    def add_bytes(n: int):
        state.bytes_done += n
        report()

    started = time.time()
    all_jobs: list[tuple[str, FileJob]] = []
    for sj in plan.systems:
        for fj in sj.files:
            all_jobs.append((sj.scan.source_name, fj))
    for fj in plan.bios_files:
        all_jobs.append(("bios", fj))

    for system, fj in all_jobs:
        if cancel and cancel():
            raise MigrationCancelled()
        state.system = system
        state.current_file = os.path.relpath(fj.target, plan.target_roms)
        report()
        if options.dry_run:
            state.bytes_done += fj.size
        else:
            try:
                _copy_file(fj, on_bytes=add_bytes, cancel=cancel)
            except MigrationCancelled:
                raise
            except OSError as exc:
                log(f"ERROR copying {fj.source}: {exc}")
        state.files_done += 1
        report()

    state.phase = "gamelist"
    for sj in plan.systems:
        if not sj.gamelist_source:
            continue
        state.system = sj.scan.source_name
        state.current_file = os.path.join(os.path.basename(sj.target_dir), "gamelist.xml")
        report()
        target_xml = os.path.join(sj.target_dir, "gamelist.xml")
        try:
            tree = rewrite_gamelist(sj.gamelist_source, sj.scan.source_name, table)
        except ET.ParseError as exc:
            log(f"ERROR: {sj.gamelist_source} is not valid XML ({exc}); copying it unchanged")
            if not options.dry_run:
                os.makedirs(sj.target_dir, exist_ok=True)
                shutil.copy2(sj.gamelist_source, target_xml)
            continue
        if os.path.isfile(target_xml) and (sj.scan.mapping.status == "merged" or not options.overwrite):
            tree = merge_gamelists(target_xml, tree)
        if options.dry_run:
            log(f"[dry-run] would write {target_xml}")
        else:
            write_gamelist(tree, target_xml)
            log(f"wrote {target_xml}")

    state.phase = "done"
    elapsed = time.time() - started
    state.message = (f"{'Dry run finished' if options.dry_run else 'Finished'}: "
                     f"{state.files_done} files, {human_size(state.bytes_done)} in {elapsed:.0f}s; "
                     f"{plan.skipped_existing} files already present were skipped.")
    report()
    return state


def format_plan(plan: Plan) -> str:
    lines = [f"Destination: {plan.target_roms}", ""]
    for sj in plan.systems:
        lines.append(f"  {sj.scan.source_name:<18} -> {os.path.basename(sj.target_dir):<18} "
                     f"{len(sj.files):>6} files {human_size(sj.total_bytes):>10}"
                     f"{'   + gamelist.xml' if sj.gamelist_source else ''}")
    if plan.bios_files:
        lines.append(f"  {'bios':<18} -> {'bios':<18} {len(plan.bios_files):>6} files "
                     f"{human_size(sum(f.size for f in plan.bios_files)):>10}")
    lines.append("")
    lines.append(f"Total: {plan.total_files} files, {human_size(plan.total_bytes)}; "
                 f"{plan.skipped_existing} files ({human_size(plan.skipped_bytes)}) already on target.")
    return "\n".join(lines)
