"""Unit tests for RetroDS (run with: python -m unittest discover -s tests -v)."""

import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrods import __version__  # noqa: E402
from retrods.library import find_roms_dir, parse_gamelist, scan_library  # noqa: E402
from retrods.migrate import (Options, build_plan, execute_plan, find_target_roms,  # noqa: E402
                             merge_gamelists, rewrite_gamelist, rewrite_path)
from retrods.releases import Image, Release, images_for_release, _escape_keep_version  # noqa: E402
from retrods.systems import build_table, map_system  # noqa: E402


def write(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def make_library(root):
    roms = os.path.join(root, "share", "roms")
    write(os.path.join(root, "share", "system", "batocera.conf"), b"")
    write(os.path.join(root, "share", "bios", "scph1001.bin"), b"BIOS")
    write(os.path.join(roms, "snes", "Mario.sfc"), b"ROM")
    write(os.path.join(roms, "snes", "images", "Mario-image.png"), b"IMG")
    write(os.path.join(roms, "snes", "videos", "Mario-video.mp4"), b"VID")
    write(os.path.join(roms, "snes", "Thumbs.db"), b"junk")
    write(os.path.join(roms, "snes", "gamelist.xml"), b"""<?xml version="1.0"?>
<gameList>
  <game><path>./Mario.sfc</path><name>Mario</name>
    <image>./images/Mario-image.png</image>
    <thumbnail>/userdata/roms/snes/images/Mario-thumb.png</thumbnail>
    <video>/userdata/roms/megadrive/videos/x.mp4</video>
  </game>
  <game><path>Zelda.sfc</path><name>Zelda</name></game>
</gameList>""")
    write(os.path.join(roms, "megadrive", "Sonic.md"), b"ROM")
    write(os.path.join(roms, "megadrive", "gamelist.xml"),
          b"<gameList><game><path>./Sonic.md</path><name>Sonic</name></game></gameList>")
    write(os.path.join(roms, "amiga500", "A.lha"), b"A")
    write(os.path.join(roms, "amiga500", "gamelist.xml"),
          b"<gameList><game><path>./A.lha</path><name>A</name></game></gameList>")
    write(os.path.join(roms, "amiga1200", "B.lha"), b"B")
    write(os.path.join(roms, "amiga1200", "gamelist.xml"),
          b"<gameList><game><path>./B.lha</path><name>B</name></game></gameList>")
    write(os.path.join(roms, "lcdgames", "x.mgw"), b"x")
    write(os.path.join(roms, "mystery", "x.bin"), b"x")
    return roms


class VersionTest(unittest.TestCase):
    def test_version_is_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


class SystemsTest(unittest.TestCase):
    def test_same_renamed_merged_unsupported_unknown(self):
        self.assertEqual(map_system("snes").status, "same")
        self.assertEqual(map_system("megadrive").target, "genesis")
        self.assertEqual(map_system("megadrive").status, "renamed")
        self.assertEqual(map_system("amiga500").status, "merged")
        self.assertEqual(map_system("amiga1200").target, "amiga")
        self.assertEqual(map_system("lcdgames").status, "unsupported")
        self.assertEqual(map_system("doesnotexist").status, "unknown")
        self.assertEqual(map_system("SNES").target, "snes")

    def test_overrides(self):
        table = build_table({"mystery": "ports", "snes": None})
        self.assertEqual(map_system("mystery", table).target, "ports")
        self.assertEqual(map_system("snes", table).status, "unsupported")


class RewriteTest(unittest.TestCase):
    def test_rewrite_path(self):
        table = build_table()
        self.assertEqual(rewrite_path("./images/a.png", "snes", table), "./images/a.png")
        self.assertEqual(rewrite_path("/userdata/roms/snes/images/a.png", "snes", table), "./images/a.png")
        self.assertEqual(rewrite_path("/userdata/roms/megadrive/images/a.png", "snes", table),
                         "/storage/roms/genesis/images/a.png")
        self.assertEqual(rewrite_path("Zelda.sfc", "snes", table), "./Zelda.sfc")
        self.assertEqual(rewrite_path("", "snes", table), "")

    def test_merge_gamelists(self):
        tmp = tempfile.mkdtemp()
        try:
            existing = os.path.join(tmp, "gamelist.xml")
            write(existing, b"<gameList><game><path>./A.lha</path><name>Old A</name></game>"
                            b"<game><path>./C.lha</path><name>C</name></game></gameList>")
            incoming = ET.ElementTree(ET.fromstring(
                "<gameList><game><path>./A.lha</path><name>New A</name></game>"
                "<game><path>./B.lha</path><name>B</name></game></gameList>"))
            merged = merge_gamelists(existing, incoming)
            names = {g.findtext("path"): g.findtext("name") for g in merged.getroot()}
            self.assertEqual(names, {"./A.lha": "New A", "./B.lha": "B", "./C.lha": "C"})
        finally:
            shutil.rmtree(tmp)


class ScanAndMigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.roms = make_library(self.tmp)
        self.target = os.path.join(self.tmp, "rocknix", "games-roms")
        os.makedirs(self.target)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_find_roms_dir_accepts_root_or_roms(self):
        self.assertEqual(find_roms_dir(os.path.join(self.tmp, "share")), self.roms)
        self.assertEqual(find_roms_dir(self.roms), self.roms)
        with self.assertRaises(FileNotFoundError):
            find_roms_dir(self.tmp + "/nope")

    def test_scan(self):
        scan = scan_library(os.path.join(self.tmp, "share"))
        self.assertEqual(scan.flavour, "batocera")
        self.assertIsNotNone(scan.bios_dir)
        by = {s.source_name: s for s in scan.systems}
        self.assertEqual(by["snes"].gamelist_games, 2)
        self.assertEqual(by["snes"].rom_files, 1)       # Thumbs.db ignored, gamelist excluded
        self.assertEqual(by["snes"].media_files, 2)
        self.assertEqual(by["megadrive"].target_name, "genesis")
        self.assertFalse(by["lcdgames"].mapping.supported)
        self.assertEqual(by["mystery"].status, "unknown")
        self.assertEqual(len(parse_gamelist(os.path.join(self.roms, "snes", "gamelist.xml"))), 2)

    def test_find_target_roms(self):
        # an empty games-roms share is itself the roms folder
        self.assertEqual(find_target_roms(self.target), self.target)
        os.makedirs(os.path.join(self.target, "roms"))
        # a card / share that contains a roms folder -> use it
        self.assertEqual(find_target_roms(self.target), os.path.join(self.target, "roms"))
        self.assertEqual(find_target_roms(os.path.join(self.target, "roms")), os.path.join(self.target, "roms"))

    def test_dry_run_writes_nothing(self):
        scan = scan_library(self.roms)
        plan = build_plan(scan, self.target, Options(dry_run=True))
        self.assertGreater(plan.total_files, 0)
        execute_plan(plan, Options(dry_run=True))
        self.assertFalse(os.path.exists(os.path.join(self.target, "snes")))

    def test_push_then_rerun_skips(self):
        scan = scan_library(os.path.join(self.tmp, "share"))
        plan = build_plan(scan, self.target, Options())
        state = execute_plan(plan, Options())
        self.assertEqual(state.phase, "done")
        roms = self.target
        self.assertTrue(os.path.isfile(os.path.join(roms, "snes", "Mario.sfc")))
        self.assertTrue(os.path.isfile(os.path.join(roms, "snes", "images", "Mario-image.png")))
        self.assertTrue(os.path.isfile(os.path.join(roms, "genesis", "Sonic.md")))
        self.assertTrue(os.path.isfile(os.path.join(roms, "amiga", "A.lha")))
        self.assertTrue(os.path.isfile(os.path.join(roms, "amiga", "B.lha")))
        self.assertTrue(os.path.isfile(os.path.join(roms, "bios", "scph1001.bin")))
        self.assertFalse(os.path.exists(os.path.join(roms, "lcdgames")))
        self.assertFalse(os.path.exists(os.path.join(roms, "mystery")))
        self.assertFalse(os.path.exists(os.path.join(roms, "snes", "Thumbs.db")))
        # gamelist rewritten
        tree = ET.parse(os.path.join(roms, "snes", "gamelist.xml"))
        games = {g.findtext("name"): g for g in tree.getroot()}
        self.assertEqual(games["Mario"].findtext("thumbnail"), "./images/Mario-thumb.png")
        self.assertEqual(games["Mario"].findtext("video"), "/storage/roms/genesis/videos/x.mp4")
        self.assertEqual(games["Zelda"].findtext("path"), "./Zelda.sfc")
        # amiga gamelists merged
        amiga = ET.parse(os.path.join(roms, "amiga", "gamelist.xml")).getroot()
        self.assertEqual(sorted(g.findtext("name") for g in amiga), ["A", "B"])
        # second run skips everything
        plan2 = build_plan(scan, self.target, Options())
        self.assertEqual(plan2.total_files, 0)
        self.assertEqual(plan2.skipped_existing, plan.total_files)

    def test_include_unknown_and_media_only(self):
        scan = scan_library(self.roms)
        plan = build_plan(scan, self.target, Options(include_unknown=True, include_roms=False, copy_bios=False))
        targets = {os.path.relpath(f.target, self.target) for sj in plan.systems for f in sj.files}
        self.assertIn(os.path.join("snes", "images", "Mario-image.png"), targets)
        self.assertNotIn(os.path.join("snes", "Mario.sfc"), targets)
        self.assertTrue(any(sj.target_dir.endswith("mystery") for sj in plan.systems))


class ReleasesTest(unittest.TestCase):
    def test_version_matching(self):
        self.assertEqual(_escape_keep_version("ROCKNIX-H700.aarch64-20261001-DDR4.img.gz"),
                         r"ROCKNIX\-H700\.aarch64\-\d{8}\-DDR4\.img\.gz")
        feed = [Image("Anbernic RG CubeXX (DDR4)", "stable", "20261001",
                      "https://x/download/20261001/ROCKNIX-H700.aarch64-20261001-DDR4.img.gz",
                      post_install="dtb.img", dtb="sun50i-h700-anbernic-rgcubexx"),
                Image("Other Device", "stable", "20261001", "https://x/download/20261001/ROCKNIX-NEW.aarch64-20261001.img.gz")]
        rel = Release("20260701", assets={
            "ROCKNIX-H700.aarch64-20260701-DDR4.img.gz": ("https://x/d/ROCKNIX-H700.aarch64-20260701-DDR4.img.gz", 10),
            "ROCKNIX-H700.aarch64-20260701-DDR4.img.gz.sha256": ("https://x/d/sha", 1),
            "ROCKNIX-H700.aarch64-20260701-DDR3.img.gz": ("https://x/d/DDR3", 10),
        })
        imgs = images_for_release(feed, rel)
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0].name, "Anbernic RG CubeXX (DDR4)")
        self.assertEqual(imgs[0].version, "20260701")
        self.assertEqual(imgs[0].sha256_url, "https://x/d/sha")
        self.assertEqual(imgs[0].dtb, "sun50i-h700-anbernic-rgcubexx")


if __name__ == "__main__":
    unittest.main()
