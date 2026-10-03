# RetroDS

**Version 0.1.0** - Author: Dan Lee

RetroDS is a small desktop tool that does two jobs the official
[ROCKNIX ImageBurner](https://github.com/ROCKNIX/ImageBurner) does not:

1. **Install any ROCKNIX version** - pick your device, pick a specific release
   (latest stable, latest nightly, or any older published release), pick the SD
   card, and RetroDS downloads, verifies the SHA-256 checksum, writes the image and
   applies the device's post-install step (dtb.img / extlinux / grubenv).
2. **Migrate a Batocera or Knulli library into ROCKNIX without re-scraping** -
   point it at your Batocera/Knulli share (for example a NAS or the device's own
   SMB share), review what it found, then push ROMs, scraped images, videos,
   manuals and the `gamelist.xml` files into the ROCKNIX `roms` share with the
   correct ROCKNIX folder names.

It is written in Python 3 with Tkinter (standard library only) and can be
packaged as a single `RetroDS.exe`.

---

## How it works

### Install tab

* The device list and the file-name pattern for each device come from the same
  feed the official ImageBurner uses (`https://releases.rocknix.org/imageburner/`).
* The version list comes from the GitHub release history of
  `ROCKNIX/distribution` (and `ROCKNIX/distribution-nightly` if you tick
  *include nightly builds*).  If the GitHub API is not reachable, the list is
  scraped from `https://releases.rocknix.org/` instead.
* Only versions that actually contain an image for the selected device are
  shown.  Older releases do not include newer devices.
* Downloads are cached in `%LOCALAPPDATA%\RetroDS\downloads` (Windows) or
  `~/.cache/RetroDS/downloads` (Linux) and re-used when the checksum matches.
* Writing the card needs **Administrator** rights on Windows (RetroDS offers to
  restart itself elevated) or **root** on Linux (`sudo`).
* Only removable / USB disks are listed unless you tick *show all disks*.  A
  non-removable disk must be confirmed by typing its device id.

### Migrate tab

Batocera, Knulli and ROCKNIX all use the same EmulationStation family, so the
`gamelist.xml` format and media tags are identical.  Batocera/Knulli store
everything under `/userdata/roms/<system>/`:

```
roms/snes/Super Mario World.sfc
roms/snes/gamelist.xml
roms/snes/images/Super Mario World-image.png
roms/snes/images/Super Mario World-thumb.png
roms/snes/videos/Super Mario World-video.mp4
roms/snes/manuals/Super Mario World-manual.pdf
```

ROCKNIX uses the same layout under `/storage/roms/<system>/`, so RetroDS:

1. finds the `roms` folder in the share you enter (share root, `userdata`, or the
   `roms` folder itself all work);
2. scans each system folder: games in `gamelist.xml`, ROM files, media files,
   size;
3. maps the folder name to the ROCKNIX name (see table below);
4. copies ROMs and media file by file, **skipping files that already exist with
   the same size**, so an interrupted run can simply be started again;
5. rewrites `gamelist.xml` so absolute Batocera paths
   (`/userdata/roms/snes/images/x.png`) become relative (`./images/x.png`) and
   merges gamelists when two source folders land in one ROCKNIX folder
   (`amiga500` + `amiga1200` -> `amiga`);
6. optionally copies the `bios` folder to `roms/bios`.

Systems ROCKNIX does not have (for example `lcdgames`, `windows`, `model3`)
are listed as *unsupported* and skipped.  Folders RetroDS has never heard of are
listed as *unknown* and skipped unless you tick *include unknown systems*.

After the push, on the ROCKNIX device press START, open **Game Settings** and
run **Update Gamelists** so EmulationStation picks everything up.

### Folder name mapping

Identical names map 1:1.  Names that differ:

| Batocera / Knulli | ROCKNIX |
|---|---|
| megadrive | genesis |
| megacd | segacd |
| sg1000 | sg-1000 |
| lynx | atarilynx |
| jaguar | atarijaguar |
| xegs | atari800 |
| wswan / wswanc | wonderswan / wonderswancolor |
| supergrafx | sgfx |
| neogeocd | neocd |
| snes-msu1 | snesmsu1 |
| amiga500, amiga1200 | amiga (merged) |
| c20 | vic20 |
| cplus4 | c16 |
| msx1, msx2, msxturbor | msx (merged) |
| odyssey2 / o2em | odyssey |
| videopacplus | videopac |
| macintosh | mac |
| colecovision | coleco |
| pico8 / tic80 | pico-8 / tic-80 |
| dos | pc |
| prboom, gzdoom, uzdoom | doom |
| quake, tyrquake, quake2, quake3 | idtech |
| eduke32, fury, raze | build |
| vgmplay | music |

To change or extend the table, copy `systems_map.example.json` to
`systems_map.json` next to `RetroDS.exe` (or the folder you run it from).
Use `null` to skip a system.

---

## Using it

### Windows (recommended workflow)

1. Download or build `RetroDS.exe` (see *Building*), right-click it and choose
   **Run as administrator**.
2. **Tab 1 - Install ROCKNIX**: click *Load device & version list*, choose your
   device and the version you want, insert the SD card, click *Refresh disks*,
   select the card, then *Download, verify and WRITE to SD card*.
3. Put the card in the handheld and boot it.  Connect it to Wi-Fi and enable
   **Samba** under *Network Settings*.  Note its IP address.  The share is called
   `games-roms`, for example `\\192.168.1.50\games-roms` (user `root`, password
   `rocknix` by default).
4. **Tab 2 - Migrate library**: enter your Batocera/Knulli share
   (`\\BATOCERA\share` or `\\NAS\batocera\roms`) and the ROCKNIX share, click
   *Scan library*, review the table (click the *Copy* column to include or exclude
   a system), click *Preview (dry run)* to see exactly what would be copied, then
   *Push to ROCKNIX*.

Why over the network and not straight onto the card?  ROCKNIX formats the games
partition as ext4, which Windows cannot write.  On Linux you can mount the card
and enter the mount point of the games partition instead.

### Command line

Everything is also available without the GUI:

```
python -m retrods releases                       # list devices and versions
python -m retrods releases --device "Anbernic RG CubeXX (DDR4)"
python -m retrods scan  \\BATOCERA\share
python -m retrods push  \\BATOCERA\share \\192.168.1.50\games-roms --dry-run
python -m retrods push  \\BATOCERA\share \\192.168.1.50\games-roms
python -m retrods disks
python -m retrods flash --device "Anbernic RG CubeXX (DDR4)" --version 20260901 --disk \\.\PHYSICALDRIVE2
```

---

## Building

Requirements: Python 3.10 or newer with tkinter (the official python.org Windows
installer includes it; on Debian/Ubuntu install `python3-tk`).

```
build_windows.bat        -> dist\RetroDS.exe  (requests admin rights on launch)
./build_linux.sh         -> dist/RetroDS
```

Run from source instead with `python -m retrods`.

## Tests

```
python -m unittest discover -s tests -v
```

The tests build a fake Batocera library in a temp folder and exercise the
scanner, the planner, the copy, the gamelist rewriting and merging, the
re-run skip logic and the version matching.

---

## Project layout

```
retrods/__init__.py     version number
retrods/__main__.py     entry point (GUI by default, CLI with sub-commands)
retrods/gui.py          Tkinter window
retrods/cli.py          command-line interface
retrods/releases.py     device feed + GitHub release history -> version catalogue
retrods/downloader.py   download with progress and SHA-256 verification
retrods/disks.py        removable disk detection (Windows PowerShell/CIM, Linux lsblk)
retrods/flasher.py      raw image writing and post-install steps
retrods/systems.py      Batocera/Knulli -> ROCKNIX folder mapping
retrods/library.py      read-only library scanner
retrods/migrate.py      copy planner / executor / gamelist rewriting
tests/                  unit tests
```

## Safety notes

* Writing an image erases the whole SD card.  RetroDS only lists removable
  disks by default and asks for confirmation before writing.
* The migration never deletes anything on the source share.  On the ROCKNIX
  side it only writes files that are missing (or all files, if you tick
  *overwrite*), and replaces `gamelist.xml` with the rewritten/merged version.
* Checksums are verified before writing; a mismatch deletes the download.

## Credits

* ROCKNIX team for the distribution, the ImageBurner feed and the device
  post-install logic this tool mirrors.
* Batocera and Knulli teams for EmulationStation and the gamelist format.

---

## Changelog

### 0.1.0 - 2026-10-03

First release.

* Install tab: device list from the ROCKNIX ImageBurner feed, version list from
  the ROCKNIX GitHub releases (with releases.rocknix.org fallback), nightly
  builds optional, download cache, SHA-256 verification, raw SD card write on
  Windows (ctypes, volume lock/dismount) and Linux, post-install steps
  (dtb.img / extlinux / grubenv), download-only and post-install-only actions.
* Migrate tab: scan Batocera/Knulli shares, per-system report with ROCKNIX
  folder mapping and status, include/exclude per system, options for ROMs,
  media, BIOS, overwrite and unknown systems, dry run, resumable copy that skips
  files already present, gamelist.xml path rewriting and merging.
* Command-line interface with `releases`, `scan`, `push`, `disks` and `flash`.
* `systems_map.json` override file for custom folder mappings.
* Unit tests, PyInstaller build scripts for Windows and Linux.
