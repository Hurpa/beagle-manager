# SPDX-License-Identifier: GPL-3.0-or-later
"""Egy szoftveres "Beagle" TCP-n, a protokolljegyzetek alapján (teszteléshez).

A valódi készülékhez hasonlóan a "PAGE n" után érkező adat végét a gzip folyam
vége jelzi; a mock ellenőrzi, hogy a kicsomagolt oldal pontosan 240064 bájt-e.
"""

import base64
import socket
import threading
import zlib

RAW_SIZE = 600 * 800 // 2 + 64

INFO = (
    "PROTOCOL VERSION=8\n"
    "FIRMWARE ID=Beagle-F-U BUILDDATE=18.April.2013 GIT=cf3dc863 IAP=0 BLUETOOTH=u.3\n"
    "DEVICE SERIAL=3266970344 BDADDR=74:E5:43:51:D4:FA DISPLAY=V110\n"
    "VCOM VALUE=1910\n"
    "SDCONTENT REVISION=2\n"
    "OPTION LOWFLASH=0 FFTBT=1\n"
    "INFOOK\n"
)


def _b64_nopad(text: str) -> str:
    return base64.b64encode(text.encode()).decode().rstrip("=")  # a készülék kitöltés nélkül válaszol


class MockBeagle(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.partner = None
        self.books = {}      # id -> {"title", "author", "pages": {n: raw bytes}}
        self.log = []        # a kapott parancssorok
        self.errors = []     # protokollhibák, amiket a mock észlelt
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            try:
                self._serve(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def stop(self):
        self._stop = True
        self.server.close()

    # ------------------------------------------------------------------

    def _serve(self, conn):
        buf = bytearray()
        current = None

        def send(text):
            conn.sendall(text.encode())

        def read_line():
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return None
                buf.extend(chunk)
            i = buf.index(b"\n")
            line = bytes(buf[:i]).decode()
            del buf[: i + 1]
            return line

        def read_gzip_page():
            dec = zlib.decompressobj(31)
            raw = bytearray()
            while not dec.eof:
                if not buf:
                    chunk = conn.recv(8192)
                    if not chunk:
                        return None
                    buf.extend(chunk)
                data = bytes(buf)
                buf.clear()
                raw += dec.decompress(data)
                if dec.eof:
                    buf.extend(dec.unused_data)   # ami a gzip után jön, a következő parancs
            return bytes(raw)

        while True:
            line = read_line()
            if line is None:
                return
            self.log.append(line)
            cmd = line.split(" ")[0]
            if cmd == "PING":
                pass
            elif cmd == "VIRGIN":
                self.books.clear()
                send("VIRGINOK\n")
            elif cmd == "GETPARTNER":
                send("NOPARTNER\n" if self.partner is None else f"PARTNER ID={self.partner}\n")
            elif cmd == "PARTNER":
                self.partner = line.split("=")[1]
                send("PARTNEROK\n")
            elif cmd == "INFO":
                send(INFO)
            elif cmd == "MEMORY":
                send(f"BOOKS USE={len(self.books)} MAXIMUM=15\n"
                     "CLUSTERS USE=21 MAXIMUM=255 SIZE=59\n"
                     "MEM TOTAL=8192 FREE=2168\nMEMORYOK\n")
            elif cmd == "GETBOOKS":
                for bid, b in self.books.items():
                    pages = sorted(b["pages"])
                    send(f"BOOK ID={bid} FIRSTPAGE={pages[0] if pages else 0} "
                         f"LASTPAGE={pages[-1] if pages else 0} CURRENTPAGE=1 "
                         f"AUTHOR={_b64_nopad(b['author'])} TITLE={_b64_nopad(b['title'])}\n")
                send("GETBOOKSOK\n")
            elif cmd == "BOOK":
                bid = line.split("=")[1]
                current = self.books.setdefault(bid, {"title": "", "author": "", "pages": {}})
                send("BOOKOK\n")
            elif cmd == "TITLE":
                current["title"] = base64.b64decode(line.split(" ")[1] + "==").decode()
                send("TITLEOK\n")
            elif cmd == "AUTHOR":
                current["author"] = base64.b64decode(line.split(" ")[1] + "==").decode()
                send("AUTHOROK\n")
            elif cmd == "PAGE":
                raw = read_gzip_page()
                if raw is None:
                    return
                if len(raw) != RAW_SIZE or any(raw[-64:]):
                    self.errors.append(f"rossz oldal: {len(raw)} bájt")
                current["pages"][int(line.split(" ")[1])] = raw
                send("PAGEOK\n")
            elif cmd == "ENDBOOK":
                send("ENDBOOKOK\n")
                current = None
            elif cmd == "DELETEBOOK":
                bid = line.split("=")[1]
                if bid in self.books:
                    del self.books[bid]
                    send("DELETEBOOKOK\n")
                else:
                    send("DELETEBOOKERROR\n")
            elif cmd == "QUIT":
                send("QUITOK\n")
                return
            else:
                self.errors.append(f"ismeretlen parancs: {line!r}")
