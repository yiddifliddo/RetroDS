"""Tkinter desktop GUI for RetroDS.

Tab 1 "Install ROCKNIX": pick device, version and SD card, download, verify,
write, post-install.
Tab 2 "Migrate library": scan a Batocera/Knulli share, review the plan and
push ROMs, scraped media and gamelists into the ROCKNIX ``roms`` folder.

All long operations run in a worker thread and report back through a queue so
the window stays responsive.
"""

from __future__ import annotations

import copy
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from . import APP_NAME, __version__
from .library import LibraryScan, human_size, scan_library
from .migrate import MigrationCancelled, Options, build_plan, execute_plan, format_plan
from .systems import load_overrides

WINDOWS = os.name == "nt"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__} - ROCKNIX installer & library migrator")
        self.geometry("980x720")
        self.minsize(820, 600)
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self.catalog = None
        self.disks = []
        self.scan: LibraryScan | None = None
        self.overrides = {}
        try:
            self.overrides = load_overrides()
        except Exception as exc:  # noqa: BLE001
            self.after(200, lambda: self.log(f"systems_map.json ignored: {exc}"))

        self._build()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if WINDOWS else "clam")
        except tk.TclError:
            pass
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.tab_install = ttk.Frame(self.notebook, padding=10)
        self.tab_migrate = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_install, text="  1. Install ROCKNIX  ")
        self.notebook.add(self.tab_migrate, text="  2. Migrate library  ")
        self._build_install()
        self._build_migrate()

        bottom = ttk.Frame(self, padding=(8, 4))
        bottom.pack(fill="both", expand=False)
        ttk.Label(bottom, text="Log").pack(anchor="w")
        self.logbox = ScrolledText(bottom, height=9, state="disabled", wrap="word", font=("Consolas" if WINDOWS else "monospace", 9))
        self.logbox.pack(fill="both", expand=True)
        self.log(f"{APP_NAME} {__version__} ready. Author: Dan Lee.")
        if self.overrides:
            self.log(f"Loaded {len(self.overrides)} system name overrides from systems_map.json")

    def _build_install(self) -> None:
        f = self.tab_install
        f.columnconfigure(1, weight=1)
        row = 0
        ttk.Label(f, text="Step 1 - Choose the ROCKNIX image", font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        self.var_nightly = tk.BooleanVar(value=False)
        bar = ttk.Frame(f)
        bar.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 8))
        self.btn_load = ttk.Button(bar, text="Load device & version list", command=self.load_catalog)
        self.btn_load.pack(side="left")
        ttk.Checkbutton(bar, text="include nightly builds", variable=self.var_nightly).pack(side="left", padx=10)
        self.lbl_catalog = ttk.Label(bar, text="")
        self.lbl_catalog.pack(side="left", padx=10)
        row += 1
        ttk.Label(f, text="Device:").grid(row=row, column=0, sticky="e", padx=(0, 6), pady=2)
        self.cmb_device = ttk.Combobox(f, state="readonly", values=[])
        self.cmb_device.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        self.cmb_device.bind("<<ComboboxSelected>>", lambda e: self._device_changed())
        row += 1
        ttk.Label(f, text="Version:").grid(row=row, column=0, sticky="e", padx=(0, 6), pady=2)
        self.cmb_version = ttk.Combobox(f, state="readonly", values=[])
        self.cmb_version.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        self.cmb_version.bind("<<ComboboxSelected>>", lambda e: self._version_changed())
        row += 1
        self.lbl_image = ttk.Label(f, text="", foreground="#555")
        self.lbl_image.grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1

        ttk.Separator(f).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        ttk.Label(f, text="Step 2 - Choose the SD card", font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        bar2 = ttk.Frame(f)
        bar2.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 4))
        ttk.Button(bar2, text="Refresh disks", command=self.refresh_disks).pack(side="left")
        self.var_all_disks = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="show all disks (dangerous)", variable=self.var_all_disks, command=self.refresh_disks).pack(side="left", padx=10)
        row += 1
        ttk.Label(f, text="SD card:").grid(row=row, column=0, sticky="e", padx=(0, 6), pady=2)
        self.cmb_disk = ttk.Combobox(f, state="readonly", values=[])
        self.cmb_disk.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        row += 1

        ttk.Separator(f).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        ttk.Label(f, text="Step 3 - Download and write", font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Label(f, text="Download folder:").grid(row=row, column=0, sticky="e", padx=(0, 6), pady=2)
        from .downloader import default_download_dir
        self.var_dl_dir = tk.StringVar(value=default_download_dir())
        ttk.Entry(f, textvariable=self.var_dl_dir).grid(row=row, column=1, sticky="ew", pady=2)
        ttk.Button(f, text="Browse...", command=lambda: self._browse_dir(self.var_dl_dir)).grid(row=row, column=2, padx=(6, 0))
        row += 1
        self.var_postinstall = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="apply the device's post-install step after writing (dtb.img / extlinux / grubenv)", variable=self.var_postinstall).grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1
        bar3 = ttk.Frame(f)
        bar3.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self.btn_write = ttk.Button(bar3, text="Download, verify and WRITE to SD card", command=self.start_flash)
        self.btn_write.pack(side="left")
        ttk.Button(bar3, text="Download only", command=lambda: self.start_flash(download_only=True)).pack(side="left", padx=6)
        ttk.Button(bar3, text="Post-install only", command=self.start_postinstall).pack(side="left", padx=6)
        self.btn_cancel_install = ttk.Button(bar3, text="Cancel", command=self.cancel, state="disabled")
        self.btn_cancel_install.pack(side="left", padx=6)
        row += 1
        self.pb_install = ttk.Progressbar(f, mode="determinate", maximum=1000)
        self.pb_install.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        row += 1
        self.lbl_install = ttk.Label(f, text="Idle.")
        self.lbl_install.grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        if not WINDOWS:
            ttk.Label(f, text="On Linux run RetroDS with sudo to write SD cards.", foreground="#a60").grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_migrate(self) -> None:
        f = self.tab_migrate
        f.columnconfigure(1, weight=1)
        f.rowconfigure(6, weight=1)
        ttk.Label(f, text="Copy ROMs, scraped artwork, videos and gamelist.xml from Batocera / Knulli into ROCKNIX without re-scraping.",
                  wraplength=900).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(f, text="Batocera / Knulli share:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        self.var_source = tk.StringVar(value=r"\\BATOCERA\share" if WINDOWS else "")
        ttk.Entry(f, textvariable=self.var_source).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(f, text="Browse...", command=lambda: self._browse_dir(self.var_source)).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(f, text="ROCKNIX share (games-roms):").grid(row=2, column=0, sticky="e", padx=(0, 6), pady=2)
        self.var_target = tk.StringVar(value=r"\\ROCKNIX\games-roms" if WINDOWS else "")
        ttk.Entry(f, textvariable=self.var_target).grid(row=2, column=1, sticky="ew", pady=2)
        ttk.Button(f, text="Browse...", command=lambda: self._browse_dir(self.var_target)).grid(row=2, column=2, padx=(6, 0))

        opts = ttk.Frame(f)
        opts.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.var_roms = tk.BooleanVar(value=True)
        self.var_media = tk.BooleanVar(value=True)
        self.var_bios = tk.BooleanVar(value=True)
        self.var_overwrite = tk.BooleanVar(value=False)
        self.var_unknown = tk.BooleanVar(value=False)
        for text, var in (("copy ROMs", self.var_roms), ("copy scraped media", self.var_media),
                          ("copy BIOS folder", self.var_bios), ("overwrite existing files", self.var_overwrite),
                          ("include unknown systems", self.var_unknown)):
            ttk.Checkbutton(opts, text=text, variable=var).pack(side="left", padx=(0, 12))

        bar = ttk.Frame(f)
        bar.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 4))
        self.btn_scan = ttk.Button(bar, text="Scan library", command=self.start_scan)
        self.btn_scan.pack(side="left")
        self.btn_dry = ttk.Button(bar, text="Preview (dry run)", command=lambda: self.start_push(dry_run=True), state="disabled")
        self.btn_dry.pack(side="left", padx=6)
        self.btn_push = ttk.Button(bar, text="Push to ROCKNIX", command=lambda: self.start_push(dry_run=False), state="disabled")
        self.btn_push.pack(side="left", padx=6)
        self.btn_cancel_migrate = ttk.Button(bar, text="Cancel", command=self.cancel, state="disabled")
        self.btn_cancel_migrate.pack(side="left", padx=6)
        ttk.Label(bar, text="  Click the first column to include / exclude a system.", foreground="#555").pack(side="left")

        cols = ("inc", "source", "target", "status", "games", "media", "files", "size")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse", height=12)
        headings = {"inc": "Copy", "source": "Batocera folder", "target": "ROCKNIX folder", "status": "Status",
                    "games": "Games", "media": "Media files", "files": "Files", "size": "Size"}
        widths = {"inc": 50, "source": 150, "target": 150, "status": 100, "games": 70, "media": 90, "files": 70, "size": 90}
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="center" if c in ("inc", "games", "media", "files", "size") else "w", stretch=(c in ("source", "target")))
        self.tree.grid(row=6, column=0, columnspan=3, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        sb.grid(row=6, column=3, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.tag_configure("off", foreground="#999")
        self.tree.tag_configure("warn", foreground="#a60")
        self.include: dict[str, bool] = {}

        self.pb_migrate = ttk.Progressbar(f, mode="determinate", maximum=1000)
        self.pb_migrate.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        self.lbl_migrate = ttk.Label(f, text="Enter the share paths and click 'Scan library'.")
        self.lbl_migrate.grid(row=8, column=0, columnspan=3, sticky="w")

    # ------------------------------------------------------------- helpers --
    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(initialdir=var.get() or os.path.expanduser("~"))
        if d:
            var.set(os.path.normpath(d))

    def log(self, text: str) -> None:
        self.logbox.configure(state="normal")
        self.logbox.insert("end", text.rstrip() + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _post(self, kind: str, **payload) -> None:
        self._queue.put((kind, payload))

    def _poll(self) -> None:
        try:
            while True:
                kind, p = self._queue.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if handler:
                    handler(**p)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run_worker(self, target, *args) -> bool:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo(APP_NAME, "Another operation is still running.")
            return False
        self._cancel.clear()
        self._worker = threading.Thread(target=self._guard, args=(target,) + args, daemon=True)
        self._worker.start()
        return True

    def _guard(self, target, *args) -> None:
        try:
            target(*args)
        except MigrationCancelled:
            self._post("log", text="Cancelled.")
            self._post("busy", busy=False)
        except Exception as exc:  # noqa: BLE001
            self._post("log", text="ERROR: " + str(exc))
            self._post("log", text=traceback.format_exc())
            self._post("error", text=str(exc))
            self._post("busy", busy=False)

    def cancel(self) -> None:
        self._cancel.set()
        self.log("Cancelling...")

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "An operation is still running. Quit anyway?"):
                return
            self._cancel.set()
        self.destroy()

    # ------------------------------------------------------ queue handlers --
    def _on_log(self, text: str) -> None:
        self.log(text)

    def _on_error(self, text: str) -> None:
        messagebox.showerror(APP_NAME, text)

    def _on_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for b in (self.btn_write, self.btn_load, self.btn_scan):
            b.configure(state=state)
        for b in (self.btn_cancel_install, self.btn_cancel_migrate):
            b.configure(state="normal" if busy else "disabled")
        if not busy:
            has_scan = self.scan is not None
            self.btn_dry.configure(state="normal" if has_scan else "disabled")
            self.btn_push.configure(state="normal" if has_scan else "disabled")
        else:
            self.btn_dry.configure(state="disabled")
            self.btn_push.configure(state="disabled")

    def _on_install_progress(self, done: int, total: int, text: str) -> None:
        self.pb_install["value"] = int(1000 * done / total) if total else 0
        self.lbl_install.configure(text=text)

    def _on_catalog(self, catalog) -> None:
        self.catalog = catalog
        self.cmb_device["values"] = catalog.devices
        self.lbl_catalog.configure(text=f"{len(catalog.devices)} devices, {len(catalog.releases)} releases ({catalog.source})")
        if catalog.devices:
            self.cmb_device.current(0)
            self._device_changed()
        self._on_busy(False)

    def _on_disks(self, disks) -> None:
        self.disks = disks
        self.cmb_disk["values"] = [d.label for d in disks]
        if disks:
            self.cmb_disk.current(0)
        else:
            self.cmb_disk.set("")
            self.log("No removable disks found. Insert the SD card and click 'Refresh disks'.")

    def _on_scan(self, scan: LibraryScan) -> None:
        self.scan = scan
        self.tree.delete(*self.tree.get_children())
        self.include.clear()
        for s in scan.systems:
            inc = s.mapping.supported or (s.status == "unknown" and self.var_unknown.get())
            self.include[s.source_name] = inc
            tag = "" if s.mapping.supported else "warn"
            self.tree.insert("", "end", iid=s.source_name, values=(
                "yes" if inc else "no", s.source_name,
                s.target_name or (s.source_name if s.status == "unknown" else "-"),
                s.status, s.gamelist_games, s.media_files, s.total_files, human_size(s.total_bytes)),
                tags=(tag if inc else "off",))
        self.lbl_migrate.configure(text=f"{scan.flavour} library at {scan.roms_dir}: {len(scan.systems)} systems, "
                                        f"{len(scan.supported)} supported, {human_size(scan.total_bytes)} total.")
        self._on_busy(False)

    def _on_migrate_progress(self, state) -> None:
        if state.phase == "planning":
            self.lbl_migrate.configure(text=f"Planning {state.system}...")
            return
        self.pb_migrate["value"] = int(1000 * state.bytes_done / state.bytes_total) if state.bytes_total else 0
        if state.phase == "done":
            self.lbl_migrate.configure(text=state.message)
            self.log(state.message)
            self._on_busy(False)
        else:
            self.lbl_migrate.configure(text=f"{state.phase}: {state.files_done}/{state.files_total} files, "
                                            f"{human_size(state.bytes_done)} of {human_size(state.bytes_total)} - {state.current_file}")

    # ------------------------------------------------------- install tab ----
    def load_catalog(self) -> None:
        nightly = self.var_nightly.get()
        self._on_busy(True)
        self.lbl_catalog.configure(text="loading...")

        def work():
            from .releases import load_catalog
            cat = load_catalog(include_nightly=nightly)
            self._post("log", text=f"Loaded {len(cat.devices)} devices and {len(cat.releases)} releases from {cat.source}")
            self._post("catalog", catalog=cat)
        self._run_worker(work)

    def _device_changed(self) -> None:
        if not self.catalog:
            return
        versions = self.catalog.versions_for_device(self.cmb_device.get())
        self.cmb_version["values"] = versions
        if versions:
            self.cmb_version.current(0)
        self._version_changed()

    def _version_changed(self) -> None:
        img = self._selected_image()
        if img:
            extra = f"  post-install: {img.post_install} ({img.dtb})" if img.post_install else ""
            self.lbl_image.configure(text=f"{img.filename}{'  ' + human_size(img.size) if img.size else ''}{extra}")
        else:
            self.lbl_image.configure(text="")

    def _selected_image(self):
        if not self.catalog or not self.cmb_device.get() or not self.cmb_version.get():
            return None
        return self.catalog.image(self.cmb_device.get(), self.cmb_version.get())

    def refresh_disks(self) -> None:
        show_all = self.var_all_disks.get()

        def work():
            from .disks import list_disks
            self._post("disks", disks=list_disks(show_all=show_all))
        self._run_worker(work)

    def _selected_disk(self):
        idx = self.cmb_disk.current()
        if idx < 0 or idx >= len(self.disks):
            return None
        return self.disks[idx]

    def start_flash(self, download_only: bool = False) -> None:
        img = self._selected_image()
        if img is None:
            messagebox.showwarning(APP_NAME, "Load the device list and choose a device and version first.")
            return
        disk = None
        if not download_only:
            disk = self._selected_disk()
            if disk is None:
                messagebox.showwarning(APP_NAME, "Choose the SD card to write to.")
                return
            from .disks import is_admin, relaunch_as_admin
            if not is_admin():
                if WINDOWS and messagebox.askyesno(APP_NAME, "Writing an SD card needs Administrator rights.\n\nRestart RetroDS as Administrator now?"):
                    if relaunch_as_admin():
                        self.destroy()
                        return
                messagebox.showerror(APP_NAME, "RetroDS must run as Administrator (Windows) or root (Linux) to write SD cards.")
                return
            warn = (f"EVERYTHING on this disk will be erased:\n\n{disk.label}\n\n"
                    f"Image: {img.filename}\n\nContinue?")
            if not messagebox.askyesno(APP_NAME, warn, icon="warning", default="no"):
                return
            if disk.system or not disk.removable:
                typed = simpledialog.askstring(APP_NAME, f"This is NOT a removable disk. Type the disk id exactly to confirm:\n{disk.device}")
                if typed != disk.device:
                    self.log("Write aborted: disk id did not match.")
                    return
        dl_dir = self.var_dl_dir.get()
        post = self.var_postinstall.get()
        self._on_busy(True)
        self.pb_install["value"] = 0

        def work():
            from .downloader import download, fetch_sha256
            from .flasher import flash
            cancel = self._cancel.is_set
            sha = None
            if img.sha256_url:
                self._post("install_progress", done=0, total=1, text="Fetching checksum...")
                sha = fetch_sha256(img.sha256_url)
            self._post("log", text=f"Downloading {img.url}")
            path = download(img.url, dl_dir, expected_sha256=sha, cancel=cancel,
                            progress=lambda d, t: self._post("install_progress", done=d, total=t, text=f"Downloading / verifying {img.filename}: {human_size(d)} of {human_size(t)}"))
            self._post("log", text=f"Image ready: {path}" + (" (checksum OK)" if sha else " (no checksum published)"))
            if download_only:
                self._post("install_progress", done=1, total=1, text=f"Downloaded to {path}")
                self._post("busy", busy=False)
                return
            self._post("log", text=f"Writing to {disk.device} ...")
            res = flash(path, disk.device, disk.volumes, image=img, post_install=post, cancel=cancel,
                        progress=lambda d, t, m: self._post("install_progress", done=d, total=t, text=m))
            msg = f"Done: wrote {human_size(res.bytes_written)} in {res.seconds:.0f}s. {res.post_install}"
            self._post("log", text=msg)
            self._post("install_progress", done=1, total=1, text=msg)
            self._post("busy", busy=False)
            self._post("info", text=msg + "\n\nYou can now remove the card, boot the device, enable Samba in Network Settings and use tab 2 to migrate your library.")
        self._run_worker(work)

    def _on_info(self, text: str) -> None:
        messagebox.showinfo(APP_NAME, text)

    def start_postinstall(self) -> None:
        img = self._selected_image()
        if img is None or not img.post_install:
            messagebox.showinfo(APP_NAME, "The selected image has no post-install step.")
            return
        disk = self._selected_disk()
        device = disk.device if disk else ""
        self._on_busy(True)

        def work():
            from .flasher import apply_post_install
            msg = apply_post_install(img, device)
            self._post("log", text=msg)
            self._post("install_progress", done=1, total=1, text=msg)
            self._post("busy", busy=False)
        self._run_worker(work)

    # ------------------------------------------------------- migrate tab ----
    def _tree_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid or self.scan is None:
            return
        self.include[iid] = not self.include.get(iid, False)
        sys_scan = next((s for s in self.scan.systems if s.source_name == iid), None)
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "yes" if self.include[iid] else "no"
        tag = "" if (sys_scan and sys_scan.mapping.supported) else "warn"
        self.tree.item(iid, values=vals, tags=(tag if self.include[iid] else "off",))

    def start_scan(self) -> None:
        source = self.var_source.get().strip()
        if not source:
            messagebox.showwarning(APP_NAME, "Enter the Batocera / Knulli share path first.")
            return
        self._on_busy(True)
        self.scan = None
        overrides = self.overrides

        def work():
            scan = scan_library(source, overrides,
                                progress=lambda n, i, t: self._post("migrate_progress", state=_Planning(n)))
            from .library import format_report
            self._post("log", text=format_report(scan))
            self._post("scan", scan=scan)
        self._run_worker(work)

    def start_push(self, dry_run: bool) -> None:
        if self.scan is None:
            return
        target = self.var_target.get().strip()
        if not target:
            messagebox.showwarning(APP_NAME, "Enter the ROCKNIX share path first.")
            return
        selected = [s for s in self.scan.systems if self.include.get(s.source_name)]
        if not selected and not (self.var_bios.get() and self.scan.bios_dir):
            messagebox.showwarning(APP_NAME, "No systems are selected.")
            return
        opts = Options(dry_run=dry_run, overwrite=self.var_overwrite.get(), copy_bios=self.var_bios.get(),
                       include_unknown=True, include_media=self.var_media.get(), include_roms=self.var_roms.get())
        sub = LibraryScan(root=self.scan.root, roms_dir=self.scan.roms_dir, bios_dir=self.scan.bios_dir,
                          flavour=self.scan.flavour, systems=selected)
        overrides = self.overrides
        self._on_busy(True)
        self.pb_migrate["value"] = 0

        def work():
            plan = build_plan(sub, target, opts, overrides, progress=lambda st: self._post("migrate_progress", state=copy.copy(st)))
            self._post("log", text=format_plan(plan))
            if plan.total_files == 0 and not any(sj.gamelist_source for sj in plan.systems):
                self._post("log", text="Nothing to copy - everything is already on the ROCKNIX share.")
                self._post("busy", busy=False)
                return
            if not dry_run:
                ok = self._ask_sync(f"Copy {plan.total_files} files ({human_size(plan.total_bytes)}) to\n{plan.target_roms} ?")
                if not ok:
                    self._post("log", text="Push cancelled by user.")
                    self._post("busy", busy=False)
                    return
            execute_plan(plan, opts, overrides, progress=lambda st: self._post("migrate_progress", state=copy.copy(st)),
                         cancel=self._cancel.is_set, log=lambda s: self._post("log", text=s))
        self._run_worker(work)

    def _ask_sync(self, text: str) -> bool:
        """Ask a yes/no question from a worker thread and wait for the answer."""
        ev = threading.Event()
        result = {"ok": False}

        def ask():
            result["ok"] = messagebox.askyesno(APP_NAME, text, icon="question", default="no")
            ev.set()
        self.after(0, ask)
        ev.wait()
        return result["ok"]


class _Planning:
    phase = "planning"

    def __init__(self, system: str) -> None:
        self.system = system


def run() -> int:
    if WINDOWS and getattr(sys, "frozen", False):
        # keep the console hidden for the frozen build; errors go to the log pane
        pass
    app = App()
    app.mainloop()
    return 0
