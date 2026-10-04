# SPDX-License-Identifier: GPL-3.0-or-later
"""Egyszerű tkinter felület a Beagle kezeléséhez."""

from __future__ import annotations

import json
import os
import queue
import random
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from . import __version__, bluetooth, render
from .protocol import BeagleConnection, BeagleError, Book
from .transfer import UploadResult, upload_book

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "beagle-manager", "config.json")

FILE_TYPES = [
    ("Dokumentumok", "*.pdf *.epub *.mobi *.fb2 *.cbz *.xps *.txt"),
    ("Minden fájl", "*.*"),
]

HINT = ("Kapcsold be a Beagle-t, és tartsd nyomva a bekapcsoló gombot, amíg kéken villog "
        "(Bluetooth mód). Előtte párosítsd a gépeddel a rendszer Bluetooth-beállításaiban.")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Feltöltési párbeszéd: cím / szerző / betűméret szerkesztése
# --------------------------------------------------------------------------

class UploadDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, filename: str, title: str, author: str,
                 reflowable: bool, font_size: int, multiple: bool):
        super().__init__(parent)
        self.title("Feltöltés")
        self.transient(parent)
        self.resizable(False, False)
        self.result: dict | None = None

        self.title_var = tk.StringVar(value=title)
        self.author_var = tk.StringVar(value=author)
        self.size_var = tk.IntVar(value=font_size)
        self.skip_asking = tk.BooleanVar(value=False)

        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text=filename, font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(frame, text="Cím").grid(row=1, column=0, sticky="w", pady=3)
        title_entry = ttk.Entry(frame, textvariable=self.title_var, width=48)
        title_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=3)
        ttk.Label(frame, text="Szerző").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.author_var, width=48).grid(
            row=2, column=1, sticky="ew", padx=(10, 0), pady=3)

        row = 3
        if reflowable:
            ttk.Label(frame, text="Betűméret").grid(row=row, column=0, sticky="w", pady=3)
            box = ttk.Frame(frame)
            box.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=3)
            ttk.Spinbox(box, from_=14, to=60, width=5, textvariable=self.size_var).pack(side="left")
            ttk.Label(box, text="  (e-bookoknál; nagyobb szám = nagyobb betű)",
                      foreground="#666").pack(side="left")
            row += 1
        if multiple:
            ttk.Checkbutton(frame, text="A többi fájlnál ne kérdezz rá",
                            variable=self.skip_asking).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Kihagyás", command=self._skip).pack(side="right")
        ok = ttk.Button(buttons, text="Feltöltés", command=self._ok, default="active")
        ok.pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._skip())
        self.protocol("WM_DELETE_WINDOW", self._skip)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        title_entry.focus_set()
        title_entry.select_range(0, "end")
        self.grab_set()
        self.wait_window()

    def _ok(self) -> None:
        try:
            size = int(self.size_var.get())
        except (tk.TclError, ValueError):
            size = render.DEFAULT_FONT_SIZE
        self.result = {
            "title": self.title_var.get().strip(),
            "author": self.author_var.get().strip(),
            "font_size": min(max(size, 12), 72),
            "skip_asking": self.skip_asking.get(),
        }
        self.destroy()

    def _skip(self) -> None:
        self.result = None
        self.destroy()


# --------------------------------------------------------------------------
# Főablak
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Beagle kezelő")
        self.geometry("780x520")
        self.minsize(660, 420)

        self.cfg = load_config()
        self.conn: BeagleConnection | None = None
        self.books: list[Book] = []
        self.busy = False
        self.uploading = False
        self.cancel = threading.Event()

        self._ui_queue: queue.Queue = queue.Queue()
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        self._build_ui()
        self._update_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._pump)
        self.after(250, self.scan)

    # ------------------------------------------------------------------ felépítés

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Hint.TLabel", foreground="#555")
        style.configure("Status.TLabel", foreground="#222")

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # -- kapcsolat
        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Készülék").grid(row=0, column=0, padx=(0, 10))
        self.addr_var = tk.StringVar(value=self.cfg.get("address", ""))
        self.device_box = ttk.Combobox(top, textvariable=self.addr_var)
        self.device_box.grid(row=0, column=1, sticky="ew")
        self.scan_btn = ttk.Button(top, text="Keresés", command=self.scan)
        self.scan_btn.grid(row=0, column=2, padx=(8, 0))
        self.connect_btn = ttk.Button(top, text="Csatlakozás", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=3, padx=(8, 0))

        self.hint_var = tk.StringVar(value=HINT)
        self.hint = ttk.Label(root, textvariable=self.hint_var, style="Hint.TLabel",
                              wraplength=720, justify="left")
        self.hint.grid(row=1, column=0, sticky="w", pady=(8, 8))
        root.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(e.width - 30, 200)))

        # -- könyvlista
        list_frame = ttk.Frame(root)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_frame, columns=("title", "author", "page"),
                                 show="headings", selectmode="browse")
        self.tree.heading("title", text="Cím")
        self.tree.heading("author", text="Szerző")
        self.tree.heading("page", text="Olvasás (oldal)")
        self.tree.column("title", width=320, anchor="w")
        self.tree.column("author", width=200, anchor="w")
        self.tree.column("page", width=110, anchor="center")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_state())
        self.tree.bind("<Delete>", lambda _e: self.delete_book())

        # -- gombok
        bar = ttk.Frame(root)
        bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.upload_btn = ttk.Button(bar, text="Feltöltés…", command=self.upload)
        self.delete_btn = ttk.Button(bar, text="Törlés", command=self.delete_book)
        self.refresh_btn = ttk.Button(bar, text="Frissítés", command=self.refresh)
        self.info_btn = ttk.Button(bar, text="Készülékinfó", command=self.show_info)
        self.cancel_btn = ttk.Button(bar, text="Megszakítás", command=self._cancel_upload)
        for btn in (self.upload_btn, self.delete_btn, self.refresh_btn, self.info_btn):
            btn.pack(side="left", padx=(0, 8))
        self.cancel_btn.pack(side="right")

        # -- állapotsor
        foot = ttk.Frame(root)
        foot.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        foot.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(foot, maximum=100, mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.status_var = tk.StringVar(value="Nincs kapcsolat.")
        ttk.Label(foot, textvariable=self.status_var, style="Status.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.storage_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.storage_var, style="Hint.TLabel").grid(
            row=1, column=1, sticky="e", pady=(6, 0))

    # ------------------------------------------------------------------ szálkezelés

    def ui(self, fn, *args) -> None:
        """Hívás a GUI-szálon (bármelyik szálból használható)."""
        self._ui_queue.put((fn, args))

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def _worker(self) -> None:
        while True:
            job = self._jobs.get()
            job()

    def run(self, job, status: str) -> None:
        """Egy műveletet a háttérszálon futtat; egyszerre csak egy fut."""
        if self.busy:
            return
        self.busy = True
        self.set_status(status)
        self._update_state()

        def wrapper() -> None:
            try:
                job()
            except render.DocumentError as exc:
                self.ui(self._fail, str(exc), False)
            except BeagleError as exc:
                self.ui(self._fail, str(exc), exc.fatal)
            except OSError as exc:
                self.ui(self._fail, f"Kapcsolati hiba: {exc}", True)
            except Exception as exc:  # váratlan hiba: a részletek a terminálra kerülnek
                traceback.print_exc()
                self.ui(self._fail, f"Váratlan hiba: {exc}", True)
            finally:
                self.ui(self._finished)

        self._jobs.put(wrapper)

    def _finished(self) -> None:
        self.busy = False
        self.uploading = False
        self.progress.configure(value=0)
        self._update_state()

    def _fail(self, message: str, drop_connection: bool) -> None:
        if drop_connection and self.conn is not None:
            conn, self.conn = self.conn, None
            threading.Thread(target=conn.close, daemon=True).start()
            self.books = []
            self._fill_books()
            self.storage_var.set("")
        self.set_status("Hiba: " + message.splitlines()[0])
        self.after(10, lambda: messagebox.showerror("Hiba", message, parent=self))

    # ------------------------------------------------------------------ állapot

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _selected_book(self) -> Book | None:
        sel = self.tree.selection()
        if not sel:
            return None
        index = self.tree.index(sel[0])
        return self.books[index] if 0 <= index < len(self.books) else None

    def _update_state(self) -> None:
        connected = self.conn is not None
        idle = not self.busy

        def enable(widget, on: bool) -> None:
            widget.state(["!disabled"] if on else ["disabled"])

        enable(self.scan_btn, idle and not connected)
        enable(self.connect_btn, idle)
        self.connect_btn.configure(text="Leválasztás" if connected else "Csatlakozás")
        self.device_box.configure(state="disabled" if connected or not idle else "normal")
        enable(self.upload_btn, connected and idle)
        enable(self.refresh_btn, connected and idle)
        enable(self.info_btn, connected and idle)
        enable(self.delete_btn, connected and idle and self._selected_book() is not None)
        enable(self.cancel_btn, self.uploading and not self.cancel.is_set())
        if connected:
            self.hint.grid_remove()
        else:
            self.hint.grid()

    def _fill_books(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for book in self.books:
            self.tree.insert("", "end", values=(
                book.title or "(cím nélkül)", book.author,
                f"{book.current_page} / {book.last_page}"))
        self._update_state()

    def _set_storage(self, memory: dict | None) -> None:
        if not memory:
            self.storage_var.set(f"Könyvek: {len(self.books)}")
            return
        parts = []
        try:
            parts.append(f"Könyvek: {int(memory['BOOKS.USE'])} / {int(memory['BOOKS.MAXIMUM'])}")
        except (KeyError, ValueError):
            parts.append(f"Könyvek: {len(self.books)}")
        try:
            used, total = int(memory["CLUSTERS.USE"]), int(memory["CLUSTERS.MAXIMUM"])
            parts.append(f"Tárhely: {round(100 * used / total)}% foglalt")
        except (KeyError, ValueError, ZeroDivisionError):
            pass
        self.storage_var.set("   ".join(parts))

    def _on_books(self, books: list[Book], memory: dict | None, status: str) -> None:
        self.books = books
        self._fill_books()
        self._set_storage(memory)
        self.set_status(status)

    # ------------------------------------------------------------------ keresés / kapcsolat

    def scan(self) -> None:
        if self.busy or self.conn is not None:
            return
        if not bluetooth.bluetooth_supported():
            self.set_status("Ez a Python nem támogatja a Bluetoothot.")
            return

        def job() -> None:
            devices = bluetooth.find_devices()
            self.ui(self._on_scan, devices)

        self.run(job, "Párosított eszközök keresése…")

    def _on_scan(self, devices: list[tuple[str, str]]) -> None:
        labels = [f"{name or 'Ismeretlen eszköz'}  ({mac})" for mac, name in devices]
        self.device_box.configure(values=labels)
        current = bluetooth.extract_mac(self.addr_var.get())
        if devices and not current:
            self.addr_var.set(labels[0])
        elif current:
            for mac, label in zip((d[0] for d in devices), labels):
                if mac == current:
                    self.addr_var.set(label)
        if devices:
            self.set_status(f"{len(devices)} párosított eszköz. Válaszd ki a Beagle-t, majd Csatlakozás.")
        else:
            self.set_status("Nem találtam párosított eszközt. Párosítsd a Beagle-t, vagy írd be a címét.")

    def toggle_connection(self) -> None:
        if self.conn is not None:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        mac = bluetooth.extract_mac(self.addr_var.get())
        if mac is None:
            messagebox.showwarning("Csatlakozás",
                                   "Add meg a Beagle Bluetooth-címét (pl. 74:E5:43:51:D4:FA), "
                                   "vagy válaszd ki a listából.", parent=self)
            return

        def job() -> None:
            conn = bluetooth.connect(
                mac, on_attempt=lambda n, total: self.ui(
                    self.set_status, f"Csatlakozás ({mac})… {n}. próbálkozás / {total}"))
            try:
                if conn.get_partner() is None:
                    self.ui(self.set_status, "Párosítási azonosító beállítása…")
                    conn.set_partner(f"{random.getrandbits(64):016X}")
                self.ui(self.set_status, "Könyvek lekérdezése…")
                books = conn.list_books()
                memory = conn.memory_or_none()
            except Exception:
                conn.close()
                raise
            self.ui(self._on_connected, conn, mac, books, memory)

        self.run(job, f"Csatlakozás ({mac})…")

    def _on_connected(self, conn: BeagleConnection, mac: str, books: list[Book],
                      memory: dict | None) -> None:
        self.conn = conn
        self.cfg["address"] = mac
        save_config(self.cfg)
        self._on_books(books, memory, f"Csatlakozva. {len(books)} könyv van a készüléken.")

    def disconnect(self) -> None:
        conn = self.conn
        if conn is None:
            return

        def job() -> None:
            conn.close()
            self.ui(self._on_disconnected)

        self.run(job, "Leválasztás…")

    def _on_disconnected(self) -> None:
        self.conn = None
        self.books = []
        self._fill_books()
        self.storage_var.set("")
        self.set_status("Nincs kapcsolat.")

    # ------------------------------------------------------------------ műveletek

    def refresh(self) -> None:
        conn = self.conn
        if conn is None:
            return

        def job() -> None:
            books = conn.list_books()
            memory = conn.memory_or_none()
            self.ui(self._on_books, books, memory, f"{len(books)} könyv van a készüléken.")

        self.run(job, "Könyvek lekérdezése…")

    def delete_book(self) -> None:
        conn, book = self.conn, self._selected_book()
        if conn is None or book is None or self.busy:
            return
        if not messagebox.askyesno("Törlés", f"Biztosan törlöd a készülékről ezt a könyvet?\n\n{book.label}",
                                   parent=self):
            return

        def job() -> None:
            conn.delete_book(book.id)
            books = conn.list_books()
            memory = conn.memory_or_none()
            self.ui(self._on_books, books, memory, "A könyv törölve.")

        self.run(job, "Törlés…")

    def show_info(self) -> None:
        conn = self.conn
        if conn is None:
            return

        def job() -> None:
            info = conn.info()
            memory = conn.memory_or_none()
            self.ui(self._show_info_dialog, info, memory)

        self.run(job, "Készülékinfó lekérdezése…")

    def _show_info_dialog(self, info: dict, memory: dict | None) -> None:
        rows = [
            ("Firmware", info.get("FIRMWARE.ID")),
            ("Firmware dátuma", info.get("FIRMWARE.BUILDDATE")),
            ("Sorozatszám", info.get("DEVICE.SERIAL")),
            ("Bluetooth-cím", info.get("DEVICE.BDADDR")),
            ("Kijelző", info.get("DEVICE.DISPLAY")),
            ("Protokollverzió", info.get("PROTOCOL.VERSION")),
        ]
        if memory:
            rows.append(("Könyvek", f"{memory.get('BOOKS.USE', '?')} / {memory.get('BOOKS.MAXIMUM', '?')}"))
            rows.append(("Tárhely (egység)", f"{memory.get('CLUSTERS.USE', '?')} / {memory.get('CLUSTERS.MAXIMUM', '?')}"))
        text = "\n".join(f"{label}: {value}" for label, value in rows if value)
        self.set_status("Kész.")
        self.after(10, lambda: messagebox.showinfo("Készülékinfó", text or "Nincs adat.", parent=self))

    # ------------------------------------------------------------------ feltöltés

    def upload(self) -> None:
        conn = self.conn
        if conn is None or self.busy:
            return
        paths = filedialog.askopenfilenames(
            parent=self, title="Feltöltendő könyvek", filetypes=FILE_TYPES,
            initialdir=self.cfg.get("last_dir") or os.path.expanduser("~"))
        if not paths:
            return
        self.cfg["last_dir"] = os.path.dirname(paths[0])
        save_config(self.cfg)

        items: list[tuple[str, str, str, int]] = []
        ask = True
        defaults: dict | None = None
        for path in paths:
            try:
                title, author, reflowable = render.peek_metadata(path)
            except render.DocumentError as exc:
                messagebox.showwarning("Kihagyva", f"{os.path.basename(path)}\n\n{exc}", parent=self)
                continue
            if ask:
                dialog = UploadDialog(self, os.path.basename(path), title, author, reflowable,
                                      (defaults or {}).get("font_size", render.DEFAULT_FONT_SIZE),
                                      multiple=len(paths) > 1)
                if dialog.result is None:
                    continue
                defaults = dialog.result
                if dialog.result["skip_asking"]:
                    ask = False
                items.append((path, dialog.result["title"] or title, dialog.result["author"],
                              dialog.result["font_size"]))
            else:
                items.append((path, title, author, (defaults or {}).get("font_size",
                                                                         render.DEFAULT_FONT_SIZE)))
        if not items:
            return

        self.cancel.clear()
        self.uploading = True

        def job() -> None:
            results: list[tuple[str, str]] = []
            failures: list[str] = []
            for number, (path, title, author, font_size) in enumerate(items, 1):
                if self.cancel.is_set():
                    break
                name = os.path.basename(path)
                prefix = f"({number}/{len(items)}) " if len(items) > 1 else ""
                self.ui(self.set_status, f"{prefix}Előkészítés: {name}…")
                try:
                    source = render.BookSource(path, title, author or None, font_size)
                except render.DocumentError as exc:
                    failures.append(f"{name}: {exc}")
                    continue
                try:
                    def progress(n: int, total: int, prefix=prefix, name=name) -> None:
                        self.ui(self._on_progress, n, total, f"{prefix}{name} – {n} / {total}. oldal")

                    result = upload_book(conn, source, progress, self.cancel)
                finally:
                    source.close()
                results.append((name, result))
                if result == UploadResult.CANCELLED:
                    break
            books = conn.list_books()
            memory = conn.memory_or_none()
            self.ui(self._on_upload_done, results, failures, books, memory)

        self.run(job, "Feltöltés indul…")
        self.uploading = True
        self._update_state()

    def _on_progress(self, number: int, total: int, text: str) -> None:
        self.progress.configure(value=100 * number / max(total, 1))
        self.set_status(text)

    def _cancel_upload(self) -> None:
        self.cancel.set()
        self.set_status("Megszakítás az aktuális oldal után…")
        self._update_state()

    def _on_upload_done(self, results: list[tuple[str, str]], failures: list[str],
                        books: list[Book], memory: dict | None) -> None:
        done = [n for n, r in results if r in (UploadResult.UPLOADED, UploadResult.RESUMED)]
        there = [n for n, r in results if r == UploadResult.ALREADY_THERE]
        cancelled = [n for n, r in results if r == UploadResult.CANCELLED]

        parts = []
        if done:
            parts.append(f"Feltöltve: {len(done)} könyv.")
        if there:
            parts.append(f"Már fent volt: {len(there)}.")
        if cancelled:
            parts.append("Megszakítva (a könyv részlegesen van fent; újra feltöltve folytatódik).")
        self._on_books(books, memory, " ".join(parts) or "Nem történt feltöltés.")
        if failures:
            self.after(10, lambda: messagebox.showwarning(
                "Néhány fájl kimaradt", "\n".join(failures), parent=self))

    # ------------------------------------------------------------------ kilépés

    def _on_close(self) -> None:
        if self.busy and self.uploading:
            if not messagebox.askyesno("Kilépés", "Feltöltés van folyamatban. Biztosan kilépsz?",
                                       parent=self):
                return
            self.cancel.set()
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    App().mainloop()
