"""Command-line interface (also handy for testing without a display).

    python -m retrods releases [--device "Anbernic RG CubeXX (DDR4)"] [--nightly]
    python -m retrods scan   <batocera-share>
    python -m retrods push   <batocera-share> <rocknix-share> [--dry-run] [--overwrite] [--no-bios]
    python -m retrods disks  [--all]
    python -m retrods flash  --device "<name>" --version <tag|latest stable|latest nightly> --disk <id> [--yes]
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .library import format_report, human_size, scan_library
from .migrate import Options, build_plan, execute_plan, format_plan
from .systems import load_overrides


def _progress_bar(done: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return f"{human_size(done)}"
    frac = min(1.0, done / total)
    return f"[{'#' * int(frac * width):<{width}}] {frac * 100:5.1f}%"


def cmd_releases(args) -> int:
    from .releases import load_catalog
    cat = load_catalog(include_nightly=args.nightly)
    print(f"Release list source: {cat.source}")
    if args.device:
        for v in cat.versions_for_device(args.device):
            img = cat.image(args.device, v)
            print(f"  {v:<32} {img.filename if img else '-'}")
        return 0
    print("Devices:")
    for d in cat.devices:
        print("  " + d)
    print("\nVersions:")
    for v in cat.versions():
        print("  " + v)
    return 0


def cmd_scan(args) -> int:
    overrides = load_overrides(args.map)
    scan = scan_library(args.source, overrides,
                        progress=lambda n, i, t: print(f"  scanning {n} ({i + 1}/{t})", file=sys.stderr))
    print(format_report(scan))
    return 0


def cmd_push(args) -> int:
    overrides = load_overrides(args.map)
    opts = Options(dry_run=args.dry_run, overwrite=args.overwrite, copy_bios=not args.no_bios,
                   include_unknown=args.include_unknown, include_media=not args.no_media,
                   include_roms=not args.no_roms)
    scan = scan_library(args.source, overrides,
                        progress=lambda n, i, t: print(f"  scanning {n} ({i + 1}/{t})", file=sys.stderr))
    print(format_report(scan))
    plan = build_plan(scan, args.target, opts, overrides)
    print()
    print(format_plan(plan))
    if plan.total_files == 0:
        print("Nothing to copy.")
        return 0
    if not args.dry_run and not args.yes:
        answer = input("Proceed with the copy? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1
    last = [""]

    def progress(p):
        line = f"\r{_progress_bar(p.bytes_done, p.bytes_total)} {p.files_done}/{p.files_total} {p.system:<14}"
        if line != last[0]:
            sys.stdout.write(line)
            sys.stdout.flush()
            last[0] = line

    state = execute_plan(plan, opts, overrides, progress=progress, log=lambda s: print("\n" + s))
    print("\n" + state.message)
    return 0


def cmd_disks(args) -> int:
    from .disks import list_disks
    for d in list_disks(show_all=args.all):
        print(d.label)
    return 0


def cmd_flash(args) -> int:
    from .disks import is_admin, list_disks
    from .downloader import download, fetch_sha256
    from .flasher import flash
    from .releases import load_catalog
    if not is_admin():
        print("Flashing needs Administrator / root privileges.", file=sys.stderr)
        return 2
    cat = load_catalog(include_nightly=True)
    img = cat.image(args.device, args.version)
    if img is None:
        print(f"No image for device '{args.device}' in version '{args.version}'.", file=sys.stderr)
        return 1
    disk = next((d for d in list_disks(show_all=True) if d.device == args.disk), None)
    if disk is None:
        print(f"Disk {args.disk} not found.", file=sys.stderr)
        return 1
    print(f"Image : {img.filename}\nDisk  : {disk.label}")
    if not args.yes:
        answer = input("ALL DATA ON THIS DISK WILL BE ERASED. Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            return 1
    sha = fetch_sha256(img.sha256_url) if img.sha256_url else None
    path = download(img.url, expected_sha256=sha,
                    progress=lambda d, t: sys.stdout.write(f"\rDownloading {_progress_bar(d, t)}"))
    print()
    res = flash(path, disk.device, disk.volumes, image=img,
                progress=lambda d, t, m: sys.stdout.write(f"\r{_progress_bar(d, t)} {m:<50}"))
    print(f"\nWrote {human_size(res.bytes_written)} in {res.seconds:.0f}s. {res.post_install}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="retrods", description=f"RetroDS {__version__}")
    p.add_argument("--version", action="version", version=f"RetroDS {__version__}")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("releases", help="list ROCKNIX devices and versions")
    r.add_argument("--device")
    r.add_argument("--nightly", action="store_true")
    r.set_defaults(func=cmd_releases)

    s = sub.add_parser("scan", help="scan a Batocera/Knulli library")
    s.add_argument("source")
    s.add_argument("--map", help="path to a systems_map.json override file")
    s.set_defaults(func=cmd_scan)

    u = sub.add_parser("push", help="copy a Batocera/Knulli library into ROCKNIX")
    u.add_argument("source")
    u.add_argument("target")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--overwrite", action="store_true")
    u.add_argument("--no-bios", action="store_true")
    u.add_argument("--no-media", action="store_true")
    u.add_argument("--no-roms", action="store_true")
    u.add_argument("--include-unknown", action="store_true")
    u.add_argument("--map")
    u.add_argument("-y", "--yes", action="store_true")
    u.set_defaults(func=cmd_push)

    d = sub.add_parser("disks", help="list removable disks")
    d.add_argument("--all", action="store_true")
    d.set_defaults(func=cmd_disks)

    f = sub.add_parser("flash", help="download and write a ROCKNIX image")
    f.add_argument("--device", required=True)
    f.add_argument("--version", required=True, dest="version")
    f.add_argument("--disk", required=True)
    f.add_argument("-y", "--yes", action="store_true")
    f.set_defaults(func=cmd_flash)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
