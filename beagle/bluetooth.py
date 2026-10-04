# SPDX-License-Identifier: GPL-3.0-or-later
"""Bluetooth: párosított Beagle keresése és RFCOMM kapcsolat (Linux, BlueZ).

A kapcsolathoz nincs szükség külső könyvtárra: a Python `socket` modulja
közvetlenül támogatja a Bluetooth RFCOMM socketet Linuxon. A párosítást a
rendszer Bluetooth-beállításaiban kell elvégezni.
"""

from __future__ import annotations

import errno
import re
import socket
import subprocess
import time

from .protocol import BeagleConnection, BeagleError

RFCOMM_CHANNEL = 1  # a jBeagle is fixen az 1-es csatornát használja

_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def bluetooth_supported() -> bool:
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


def extract_mac(text: str) -> str | None:
    match = _MAC_RE.search(text)
    return match.group(1).upper() if match else None


def _bluetoothctl_devices() -> list[tuple[str, str]]:
    """Párosított eszközök (cím, név) a `bluetoothctl`-ból."""
    for cmd in (["bluetoothctl", "devices", "Paired"], ["bluetoothctl", "devices"]):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError):
            return []
        if res.returncode == 0 and res.stdout.strip():
            devices = []
            for line in res.stdout.splitlines():
                parts = line.split(maxsplit=2)  # "Device AA:BB:.. Név"
                if len(parts) >= 2 and parts[0] == "Device" and extract_mac(parts[1]):
                    devices.append((parts[1].upper(), parts[2] if len(parts) > 2 else ""))
            return devices
    return []


def find_devices() -> list[tuple[str, str]]:
    """A párosított eszközök listája; a Beagle-nek tűnők előre kerülnek."""
    devices = _bluetoothctl_devices()
    beagles = [d for d in devices if "beagle" in d[1].lower()]
    others = [d for d in devices if d not in beagles]
    return beagles + others


# Átmeneti hibák, amiknél érdemes újrapróbálni (a készülék épp nem válaszolt).
_RETRY_ERRNOS = {errno.EHOSTDOWN, errno.EHOSTUNREACH, errno.ECONNREFUSED}

_HINTS = {
    errno.EHOSTDOWN: (
        "A gép Bluetooth-rétege nem tudott kapcsolatot felépíteni a készülékkel: a Beagle nem "
        "válaszolt. Leggyakoribb ok, hogy a csatlakozás pillanatában nincs Bluetooth módban. "
        "Kapcsold be, tartsd nyomva a bekapcsoló gombot, amíg kéken villog, és EKKOR kattints "
        "a Csatlakozásra (1-2 méteren belül)."),
    errno.EHOSTUNREACH: (
        "A készülék nem érhető el. Ellenőrizd, hogy Bluetooth módban van-e (kéken villog), "
        "és hogy a gép Bluetooth-adaptere be van-e kapcsolva."),
    errno.ECONNREFUSED: (
        "A készülék elutasította a kapcsolatot az 1-es soros csatornán. Futtasd a "
        "diagnose.py-t, az megkeresi a helyes csatornát."),
    errno.EBUSY: (
        "A kapcsolat foglalt: valószínűleg egy másik program (vagy a rendszer Bluetooth-kezelője) "
        "már csatlakozott a Beagle-hez. Szakítsd meg ott a kapcsolatot, vagy kapcsold ki-be a Beagle-t."),
    errno.EAFNOSUPPORT: (
        "A rendszer nem enged Bluetooth socketet: a Bluetooth ki van kapcsolva, nincs adapter, "
        "vagy a bluetooth szolgáltatás nem fut (sudo systemctl start bluetooth)."),
    errno.EACCES: (
        "A készülék elutasította a hitelesítést. Töröld a párosítást a rendszerben "
        "(bluetoothctl remove <cím>), majd párosítsd újra, miközben a Beagle Bluetooth módban van."),
}


def explain_error(exc: OSError) -> str:
    """Felhasználóbarát magyarázat egy csatlakozási hibához."""
    hint = _HINTS.get(exc.errno or 0,
                      "Ellenőrizd, hogy a készülék Bluetooth módban van-e (kéken villog), "
                      "hatótávon belül van, és párosítva van a gépeddel.")
    return (f"Nem sikerült csatlakozni a Beagle-hez.\n\n{hint}\n\n"
            f"Részletek: {exc}\nHa nem segít, futtasd a programmappában: python3 diagnose.py")


def connect(address: str, channel: int = RFCOMM_CHANNEL, timeout: float = 20.0,
            attempts: int = 3, on_attempt=None) -> BeagleConnection:
    """RFCOMM kapcsolat nyitása a megadott Bluetooth-címre.

    Átmeneti hibánál (a készülék épp nem válaszol) párszor újrapróbálja.
    `on_attempt(sorszám, összes)` – opcionális értesítés minden próbálkozás előtt.
    """
    if not bluetooth_supported():
        raise BeagleError("Ez a Python nem támogatja a Bluetooth socketet (AF_BLUETOOTH).")
    mac = extract_mac(address)
    if mac is None:
        raise BeagleError("Érvénytelen Bluetooth-cím (példa: 74:E5:43:51:D4:FA).")

    last: OSError | None = None
    for attempt in range(1, attempts + 1):
        if on_attempt:
            on_attempt(attempt, attempts)
        sock = None
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(timeout)
            sock.connect((mac, channel))
            return BeagleConnection(sock)
        except OSError as exc:
            if sock is not None:
                sock.close()
            last = exc
            if exc.errno not in _RETRY_ERRNOS or attempt == attempts:
                break
            time.sleep(2.0)
    assert last is not None
    raise BeagleError(explain_error(last)) from last
