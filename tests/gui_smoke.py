# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI füstteszt virtuális kijelzőn, mock Beagle ellen.

Futtatás:  xvfb-run -a -s "-screen 0 1100x700x24" python3 tests/gui_smoke.py
"""

import os
import socket
import sys
import tempfile
import time
import tkinter
from tkinter import filedialog, messagebox

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from beagle import bluetooth, gui
from beagle.protocol import BeagleConnection
from mock_beagle import MockBeagle
from test_beagle import make_epub, make_pdf

OUT = os.environ.get("BEAGLE_TEST_OUT", tempfile.gettempdir())
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
pdf, epub = os.path.join(tmp, "Próba_könyv.pdf"), os.path.join(tmp, "elbeszélés.epub")
make_pdf(pdf, pages=4)
make_epub(epub, paragraphs=60)

mock = MockBeagle()
mock.start()

# --- a Bluetooth helyett a TCP-s mock; a párbeszédek és üzenetek rögzítve
bluetooth.find_devices = lambda: [("74:E5:43:51:D4:FA", "txtr beagle"), ("AA:BB:CC:DD:EE:FF", "Fülhallgató")]
bluetooth.connect = lambda addr, *a, **k: BeagleConnection(
    socket.create_connection(("127.0.0.1", mock.port)), timeout=10)
config_dir = tempfile.mkdtemp()
gui.CONFIG_PATH = os.path.join(config_dir, "config.json")

shown = []
messagebox.showerror = lambda t, m, **k: shown.append(("error", m))
messagebox.showinfo = lambda t, m, **k: shown.append(("info", m))
messagebox.showwarning = lambda t, m, **k: shown.append(("warning", m))
messagebox.askyesno = lambda *a, **k: True
filedialog.askopenfilenames = lambda **k: (pdf, epub)

app = gui.App()
app.geometry("780x520+0+0")


def wait(cond, timeout=30):
    end = time.time() + timeout
    while not cond():
        app.update()
        time.sleep(0.02)
        if time.time() > end:
            raise TimeoutError("időtúllépés a GUI tesztben")
    app.update()


def idle():
    return not app.busy


def screenshot(name):
    app.update()
    try:
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(0, 0, 780, 520), xdisplay=os.environ.get("DISPLAY")).save(
            os.path.join(OUT, name))
    except Exception as exc:
        print("(képernyőkép nem készült:", exc, ")")


def press_ok_on_dialogs():
    for w in app.winfo_children():
        if isinstance(w, gui.UploadDialog):
            if not getattr(w, "_shot", False):
                w._shot = True
                try:
                    from PIL import ImageGrab
                    app.update()
                    ImageGrab.grab(xdisplay=os.environ.get("DISPLAY")).save(os.path.join(OUT, "gui_dialog.png"))
                except Exception:
                    pass
            w._ok()
    app.after(300, press_ok_on_dialogs)


def check(condition, text):
    print(("OK   " if condition else "HIBA ") + text)
    if not condition:
        check.failed += 1


check.failed = 0

# 1. keresés (az induláskor automatikusan lefut, kb. 250 ms késéssel)
wait(lambda: len(app.device_box.cget("values")) > 0 and idle())
check(len(app.device_box.cget("values")) == 2, "keresés: 2 párosított eszköz a listában")
check("txtr beagle" in app.addr_var.get(), "a Beagle van előre választva")
screenshot("gui_1_disconnected.png")

# 2. csatlakozás
app.connect()
wait(idle)
check(app.conn is not None, "csatlakozva")
check(mock.partner is not None, "új párosítási azonosító beállítva (volt: nincs)")
check("Könyvek: 0 / 15" in app.storage_var.get(), f"tárhelyinfó: {app.storage_var.get()!r}")
check(os.path.exists(gui.CONFIG_PATH), "a cím elmentve a konfigba")

# 3. feltöltés két fájllal (valódi párbeszéddel)
app.after(300, press_ok_on_dialogs)
app.upload()
wait(lambda: app.busy)
screenshot("gui_2_uploading.png")
wait(idle, timeout=90)
check(len(mock.books) == 2, f"2 könyv a készüléken ({len(mock.books)})")
check(mock.errors == [], f"a mock nem észlelt hibát {mock.errors}")
check(len(app.tree.get_children()) == 2, "a lista 2 sort mutat")
titles = sorted(b["title"] for b in mock.books.values())
check("Teszt könyv" in titles, f"a PDF metaadatából jött a cím: {titles}")
check(app.status_var.get().startswith("Feltöltve: 2"), f"állapotsor: {app.status_var.get()!r}")
screenshot("gui_3_connected.png")

# 4. törlés
app.tree.selection_set(app.tree.get_children()[0])
app.update()
check(app.delete_btn.instate(["!disabled"]), "kijelöléssel a Törlés gomb aktív")
app.delete_book()
wait(idle)
check(len(mock.books) == 1 and len(app.tree.get_children()) == 1, "törlés után 1 könyv")

# 5. készülékinfó
app.show_info()
wait(idle)
app.update()
time.sleep(0.1)
app.update()
check(any(k == "info" and "Beagle-F-U" in m and "V110" in m for k, m in shown), "készülékinfó megjelent")

# 6. leválasztás
app.disconnect()
wait(idle)
check(app.conn is None and app.books == [], "leválasztva")

# 7. hiba kezelése: a készülék eltűnik kapcsolat közben
app.connect()
wait(idle)
check(app.conn is not None, "újracsatlakozás sikerült")
app.conn._sock.close()                  # megszakadó kapcsolat szimulálása
app.refresh()
wait(idle)
app.update()
time.sleep(0.1)
app.update()
check(app.conn is None, "kapcsolati hiba után a program leválasztott állapotba kerül")
check(any(k == "error" for k, _ in shown), "hibaüzenet megjelent a felhasználónak")
check(app.connect_btn.cget("text") == "Csatlakozás", "újra lehet csatlakozni")
screenshot("gui_4_after_error.png")

app.destroy()
mock.stop()
print("\nEredmény:", "MINDEN RENDBEN" if check.failed == 0 else f"{check.failed} hiba")
sys.exit(1 if check.failed else 0)
