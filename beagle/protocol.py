# SPDX-License-Identifier: GPL-3.0-or-later
"""A txtr Beagle soros (RFCOMM) protokollja.

A protokoll sorokra épülő szöveges parancsokból áll (soronként egy parancs,
"\\n" lezárással). Oldalfeltöltésnél a "PAGE n" sor után nyers, gzip-pel
tömörített képadat követi, külön hosszinformáció nélkül – a készülék a gzip
folyam végéből tudja, hol ér véget az oldal.

A leírás forrásai:
  * jBeagle (Andreas Schierla, GPLv3) – BeagleConnector.java
  * opentxtr (Florian Echtler, GPLv3) – README.md jegyzetek, beagle_client.py
"""

from __future__ import annotations

import base64
import socket
from dataclasses import dataclass


class BeagleError(Exception):
    """Protokoll- vagy kapcsolati hiba (a szöveg közvetlenül megjeleníthető).

    `fatal=False` esetén a kapcsolat használható marad (pl. sikertelen törlés).
    """

    def __init__(self, message: str, fatal: bool = True):
        super().__init__(message)
        self.fatal = fatal


# --------------------------------------------------------------------------
# Base64 – a készülék kitöltés (=) nélkül válaszol, de kitöltéssel is kéri.
# --------------------------------------------------------------------------

def b64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def b64_decode(text: str) -> str:
    text = text.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded).decode("utf-8", "replace")
    except ValueError:
        return text


@dataclass
class Book:
    """Egy, a készüléken lévő könyv."""

    id: str
    title: str = ""
    author: str = ""
    first_page: int = 0
    last_page: int = 0
    current_page: int = 0

    @property
    def label(self) -> str:
        return f"{self.author}: {self.title}" if self.author else self.title


def _key_values(tokens: list[str]) -> dict[str, str]:
    """["A=1", "B=2"] -> {"A": "1", "B": "2"}"""
    out: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep:
            out[key] = value
    return out


def _to_int(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


class BeagleConnection:
    """Egy megnyitott kapcsolat a Beagle-lel.

    A `sock` bármilyen, `sendall`/`recv`/`settimeout`/`close` metódusokkal
    rendelkező socket lehet (Bluetooth RFCOMM élesben, TCP a teszteknél).
    """

    def __init__(self, sock: socket.socket, timeout: float = 30.0):
        self._sock = sock
        self._buf = bytearray()
        self.timeout = timeout
        self._sock.settimeout(timeout)

    # ------------------------------------------------------------ alacsony szint

    def _send(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise BeagleError(f"Küldési hiba: {exc}") from exc

    def _send_line(self, line: str) -> None:
        self._send(line.encode("utf-8") + b"\n")

    def _read_line(self, timeout: float | None = None) -> str:
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            while True:
                idx = self._buf.find(b"\n")
                if idx >= 0:
                    line = bytes(self._buf[:idx])
                    del self._buf[: idx + 1]
                    return line.decode("utf-8", "replace").strip()
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout as exc:
                    raise BeagleError("Időtúllépés: a készülék nem válaszol.") from exc
                except OSError as exc:
                    raise BeagleError(f"Kapcsolati hiba: {exc}") from exc
                if not chunk:
                    raise BeagleError("A készülék lezárta a kapcsolatot.")
                self._buf += chunk
        finally:
            if timeout is not None:
                self._sock.settimeout(self.timeout)

    def _expect(self, token: str, timeout: float | None = None) -> None:
        line = self._read_line(timeout)
        if line != token:
            raise BeagleError(f"Váratlan válasz a készüléktől: {line!r} (várt: {token})")

    def _read_until(self, end_token: str, timeout: float | None = None) -> list[str]:
        lines: list[str] = []
        while True:
            line = self._read_line(timeout)
            if line == end_token:
                return lines
            if line and not line.startswith("#"):
                lines.append(line)

    def _drain(self, wait: float = 0.4) -> None:
        """Eldobja a függőben lévő bejövő adatot (hiba utáni szinkronizáláshoz)."""
        self._buf.clear()
        self._sock.settimeout(wait)
        try:
            while self._sock.recv(4096):
                pass
        except OSError:
            pass
        finally:
            self._sock.settimeout(self.timeout)

    # ------------------------------------------------------------ készülékinfó

    def info(self) -> dict[str, str]:
        """Pl. {"FIRMWARE.ID": "Beagle-F-U", "DEVICE.SERIAL": "...", ...}"""
        self._send_line("INFO")
        result: dict[str, str] = {}
        for line in self._read_until("INFOOK"):
            parts = line.split()
            for key, value in _key_values(parts[1:]).items():
                result[f"{parts[0]}.{key}"] = value
        return result

    def memory(self) -> dict[str, str]:
        """Pl. {"BOOKS.USE": "1", "BOOKS.MAXIMUM": "15", "CLUSTERS.USE": ...}"""
        self._send_line("MEMORY")
        result: dict[str, str] = {}
        for line in self._read_until("MEMORYOK", timeout=10):
            parts = line.split()
            for key, value in _key_values(parts[1:]).items():
                result[f"{parts[0]}.{key}"] = value
        return result

    def memory_or_none(self) -> dict[str, str] | None:
        """Mint `memory()`, de hiba esetén None (a tárhelyadat csak kiegészítő)."""
        try:
            return self.memory()
        except BeagleError:
            self._drain()
            return None

    # ------------------------------------------------------------ párosítás

    def get_partner(self) -> str | None:
        self._send_line("GETPARTNER")
        line = self._read_line()
        if line.startswith("NOPAR"):  # a leírásban "NOPARNTER" elírás is szerepel
            return None
        if not line.startswith("PARTNER ID="):
            raise BeagleError(f"Váratlan válasz a készüléktől: {line!r}")
        return line.partition("=")[2]

    def set_partner(self, partner_id: str) -> None:
        self._send_line(f"PARTNER ID={partner_id}")
        self._expect("PARTNEROK")

    # ------------------------------------------------------------ könyvek

    def list_books(self) -> list[Book]:
        self._send_line("GETBOOKS")
        books: list[Book] = []
        for line in self._read_until("GETBOOKSOK"):
            parts = line.split()
            if parts[0] != "BOOK":
                raise BeagleError(f"Váratlan válasz a készüléktől: {line!r}")
            kv = _key_values(parts[1:])
            books.append(
                Book(
                    id=kv.get("ID", ""),
                    title=b64_decode(kv.get("TITLE", "")),
                    author=b64_decode(kv.get("AUTHOR", "")),
                    first_page=_to_int(kv.get("FIRSTPAGE")),
                    last_page=_to_int(kv.get("LASTPAGE")),
                    current_page=_to_int(kv.get("CURRENTPAGE")),
                )
            )
        return books

    def delete_book(self, book_id: str) -> None:
        self._send_line(f"DELETEBOOK ID={book_id}")
        line = self._read_line()
        if line == "DELETEBOOKERROR":
            raise BeagleError("A könyvet nem sikerült törölni.", fatal=False)
        if line != "DELETEBOOKOK":
            raise BeagleError(f"Váratlan válasz a készüléktől: {line!r}")

    # ------------------------------------------------------------ feltöltés

    def begin_book(self, book_id: str, title: str, author: str) -> None:
        """Könyv feltöltésének kezdete (utána upload_page, végül end_book).

        Ha a készüléken már van ilyen azonosítójú könyv, a feltöltés folytatódik.
        """
        self._send_line(f"BOOK ID={book_id}")
        self._expect("BOOKOK")
        self._send_line(f"TITLE {b64_encode(title)}")
        self._expect("TITLEOK")
        self._send_line(f"AUTHOR {b64_encode(author)}")
        self._expect("AUTHOROK")

    def upload_page(self, number: int, gz_data: bytes) -> None:
        """Egy (gzip-elt) oldalkép feltöltése; a 0. oldal a címlap."""
        self._send_line(f"PAGE {number}")
        self._send(gz_data)
        self._expect("PAGEOK", timeout=120)

    def upload_utility_page(self, number: int, gz_data: bytes) -> None:
        self._send_line(f"UTILITYPAGE {number}")
        self._send(gz_data)
        self._expect("PAGEOK", timeout=120)

    def end_book(self) -> None:
        self._send_line("ENDBOOK")
        self._expect("ENDBOOKOK", timeout=60)

    def factory_reset(self) -> None:
        """FIGYELEM: minden könyvet töröl a készülékről (VIRGIN)."""
        self._send_line("VIRGIN")
        self._expect("VIRGINOK", timeout=60)

    # ------------------------------------------------------------ lezárás

    def close(self) -> None:
        try:
            self._send_line("QUIT")
            self._read_line(timeout=2)
        except Exception:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
