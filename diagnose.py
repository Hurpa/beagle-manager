#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bluetooth-diagnosztika a Beagle csatlakozási gondjaihoz.

Használat:  python3 diagnose.py [BLUETOOTH-CÍM]

Előtte kapcsold a Beagle-t Bluetooth módba (bekapcsolás után tartsd nyomva a
bekapcsoló gombot, amíg kéken villog), és a script futása alatt maradjon így.
A kimenetet kérlek másold át, ha segítség kell.
"""

import errno
import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from beagle import bluetooth
from beagle.protocol import BeagleConnection, BeagleError


def run(*cmd, timeout=15):
    if not shutil.which(cmd[0]):
        return None
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (res.stdout + res.stderr).strip()
    except subprocess.SubprocessError as exc:
        return f"(hiba: {exc})"


def title(text):
    print(f"\n=== {text}")


def try_connect(mac, channel, timeout=15):
    sock = None
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(timeout)
        sock.connect((mac, channel))
        return sock, None
    except OSError as exc:
        if sock:
            sock.close()
        return None, exc


def main():
    title("1. Python Bluetooth-támogatás")
    print("AF_BLUETOOTH:", hasattr(socket, "AF_BLUETOOTH"), "| Python", sys.version.split()[0])
    if not bluetooth.bluetooth_supported():
        sys.exit("Ez a Python nem támogatja a Bluetooth socketet – használd a rendszer python3-át.")

    title("2. Bluetooth-adapter")
    out = run("bluetoothctl", "show")
    if out is None:
        print("A bluetoothctl nincs telepítve (sudo apt install bluez).")
    else:
        for line in out.splitlines():
            if any(k in line for k in ("Controller", "Powered", "Pairable", "Discoverable", "Name:")):
                print(line.strip())

    title("3. Párosított eszközök")
    devices = bluetooth.find_devices()
    for mac, name in devices:
        print(f"  {mac}  {name}")
    if not devices:
        print("  (nincs párosított eszköz)")
    mac = bluetooth.extract_mac(sys.argv[1]) if len(sys.argv) > 1 else (devices[0][0] if devices else None)
    if not mac:
        sys.exit("Add meg a Beagle címét: python3 diagnose.py 74:E5:43:51:D4:FA")
    print("Vizsgált eszköz:", mac)

    title("4. Az eszköz adatai a rendszerben")
    out = run("bluetoothctl", "info", mac) or "(nem érhető el)"
    for line in out.splitlines():
        if any(k in line for k in ("Name", "Paired", "Bonded", "Trusted", "Blocked", "Connected",
                                   "UUID", "RSSI", "Class")):
            print(line.strip())

    title("5. Kapcsolódás az 1-es soros csatornán (3 próba)")
    sock, err = None, None
    for attempt in range(1, 4):
        sock, err = try_connect(mac, bluetooth.RFCOMM_CHANNEL)
        if sock:
            print(f"  {attempt}. próba: SIKERES")
            break
        print(f"  {attempt}. próba: {err}")
        time.sleep(2)

    if not sock and err is not None and err.errno == errno.ECONNREFUSED:
        title("5b. Az 1-es csatorna elutasítva – a többi csatorna végigpróbálása")
        for channel in range(2, 31):
            sock, err2 = try_connect(mac, channel, timeout=5)
            if sock:
                print(f"  A(z) {channel}. csatorna nyitva! Ezt kell használni.")
                break
        else:
            print("  Egyik csatorna sem nyílt meg.")

    if not sock:
        title("Összegzés")
        code = err.errno if err else None
        if code == errno.EAFNOSUPPORT:
            print("A rendszer nem enged Bluetooth socketet (97): a Bluetooth ki van kapcsolva, "
                  "nincs adapter, vagy a bluetooth szolgáltatás nem fut.\n"
                  "Próbáld: sudo systemctl start bluetooth ; rfkill list")
        elif code == errno.EHOSTDOWN:
            print("Host is down (112): a Bluetooth-kapcsolat sem épül fel a készülékkel.\n"
                  "- Biztos Bluetooth módban volt a Beagle (kéken villog) a script futásakor?\n"
                  "- Próbáld közelebb vinni, és próbáld újra közvetlenül a módba kapcsolás után.\n"
                  "- Ha a párosítás régi: bluetoothctl remove " + mac + " – majd párosítsd újra,\n"
                  "  miközben a Beagle Bluetooth módban van.\n"
                  "- Ellenőrizd az alábbival, hogy a gép és a készülék egyáltalán eléri-e egymást:\n"
                  "  bluetoothctl connect " + mac)
        else:
            print("Hiba:", err)
        sys.exit(1)

    title("6. Protokoll-teszt (csak olvasó parancsok)")
    try:
        conn = BeagleConnection(sock, timeout=15)
        print("INFO:", conn.info())
        print("Párosítási azonosító:", conn.get_partner())
        for book in conn.list_books():
            print("Könyv:", book.id, "|", book.label, f"| oldalak: {book.first_page}-{book.last_page}")
        print("MEMORY:", conn.memory_or_none())
        conn.close()
        print("\nMinden rendben: a kapcsolat és a protokoll működik.")
    except BeagleError as exc:
        print("Protokollhiba:", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
