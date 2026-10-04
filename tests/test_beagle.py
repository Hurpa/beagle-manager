# SPDX-License-Identifier: GPL-3.0-or-later
"""Futtatás:  python3 -m unittest discover -s tests -v"""

import os
import socket
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pymupdf
from PIL import Image

from beagle import render
from beagle.protocol import BeagleConnection, BeagleError, b64_decode, b64_encode
from beagle.transfer import UploadResult, upload_book
from mock_beagle import RAW_SIZE, MockBeagle


def make_pdf(path, pages=5, title="Teszt könyv", author="Próba Elek"):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=420, height=595)   # A5
        page.insert_text((50, 80), f"{i + 1}. oldal – árvíztűrő tükörfúrógép ÁÉŐŰ", fontsize=18)
        page.insert_textbox(pymupdf.Rect(50, 120, 370, 500), "Lorem ipsum dolor sit amet. " * 40,
                            fontsize=12)
    doc.set_metadata({"title": title, "author": author})
    doc.set_toc([[1, "Első fejezet", 1], [1, "Második fejezet", 3]])
    doc.save(path)


def make_epub(path, paragraphs=120):
    body = "".join(f"<p>{i}. bekezdés. Árvíztűrő tükörfúrógép, " + "szöveg " * 30 + "</p>"
                   for i in range(paragraphs))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>")
        z.writestr("content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
                   'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   "<dc:title>EPUB teszt</dc:title><dc:creator>Szerző Sándor</dc:creator>"
                   '<dc:identifier id="id">x</dc:identifier><dc:language>hu</dc:language></metadata>'
                   '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
                   '<spine toc="ncx"><itemref idref="c1"/></spine></package>')
        z.writestr("toc.ncx",
                   '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                   '<head/><docTitle><text>EPUB teszt</text></docTitle><navMap><navPoint id="n1" playOrder="1">'
                   '<navLabel><text>1</text></navLabel><content src="c1.xhtml"/></navPoint></navMap></ncx>')
        z.writestr("c1.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                   f"<head><title>c1</title></head><body><h1>Első fejezet</h1>{body}</body></html>")


class EncodingTests(unittest.TestCase):
    def test_pack_matches_opentxtr_algorithm(self):
        gray = bytes([255, 0, 128, 64, 200, 31, 32, 255])
        packed = render.pack_4bpp(gray)
        expected = bytes(((gray[i] & 0xE0) | ((gray[i + 1] & 0xE0) >> 4)) for i in range(0, 8, 2))
        self.assertEqual(packed, expected)
        self.assertTrue(all(b & 0x11 == 0 for b in packed))   # alsó bit mindig 0

    def test_encode_decode_roundtrip(self):
        img = Image.new("L", (600, 800), 255)
        for x in range(600):
            for y in range(0, 800, 40):
                img.putpixel((x, y), x * 255 // 599)
        data = render.encode_page(img)
        self.assertEqual(data[:2], b"\x1f\x8b")                # gzip fejléc
        back = render.decode_page(data)
        self.assertEqual(back.size, (600, 800))
        for x in (0, 150, 300, 599):
            self.assertEqual(back.getpixel((x, 0)), ((x * 255 // 599) & 0xE0) & 0xF0)

    def test_wrong_size_rejected(self):
        with self.assertRaises(ValueError):
            render.encode_page(Image.new("L", (100, 100)))

    def test_base64_tolerates_missing_padding(self):
        self.assertEqual(b64_decode("S3VyemFubGVpdHVuZw"), "Kurzanleitung")
        self.assertEqual(b64_decode(b64_encode("Árvíztűrő")), "Árvíztűrő")


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockBeagle()
        self.mock.start()
        sock = socket.create_connection(("127.0.0.1", self.mock.port))
        self.conn = BeagleConnection(sock, timeout=10)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.conn.close()
        self.mock.stop()
        self.tmp.cleanup()

    def test_info_memory_partner(self):
        info = self.conn.info()
        self.assertEqual(info["FIRMWARE.ID"], "Beagle-F-U")
        self.assertEqual(info["DEVICE.DISPLAY"], "V110")
        mem = self.conn.memory()
        self.assertEqual(mem["BOOKS.MAXIMUM"], "15")
        self.assertIsNone(self.conn.get_partner())
        self.conn.set_partner("0123456789ABCDEF")
        self.assertEqual(self.conn.get_partner(), "0123456789ABCDEF")

    def test_pdf_upload_list_delete(self):
        pdf = os.path.join(self.tmp.name, "konyv.pdf")
        make_pdf(pdf, pages=5)
        src = render.BookSource(pdf)
        self.assertEqual((src.title, src.author), ("Teszt könyv", "Próba Elek"))
        self.assertEqual(src.page_count, 5)
        self.assertEqual(len(src.book_id), 16)

        seen = []
        result = upload_book(self.conn, src, progress=lambda n, t: seen.append((n, t)))
        self.assertEqual(result, UploadResult.UPLOADED)
        self.assertEqual(seen, [(n, 5) for n in range(6)])      # 0. címlap + 5 oldal
        self.assertEqual(self.mock.errors, [])

        books = self.conn.list_books()
        self.assertEqual(len(books), 1)
        self.assertEqual((books[0].title, books[0].author), ("Teszt könyv", "Próba Elek"))
        self.assertEqual(books[0].id, src.book_id)
        self.assertEqual(sorted(self.mock.books[src.book_id]["pages"]), [0, 1, 2, 3, 4, 5])

        # a feltöltött oldalt vissza tudjuk olvasni képként is (szemrevételezéshez)
        out = os.environ.get("BEAGLE_TEST_OUT")
        if out:
            os.makedirs(out, exist_ok=True)
            for n in (0, 1, 5):
                raw = self.mock.books[src.book_id]["pages"][n]
                px = bytearray(480000)
                px[0::2] = bytes(b & 0xF0 for b in raw[:240000])
                px[1::2] = bytes((b & 0x0F) << 4 for b in raw[:240000])
                Image.frombytes("L", (600, 800), bytes(px)).save(os.path.join(out, f"pdf_page{n}.png"))

        self.conn.delete_book(books[0].id)
        self.assertEqual(self.conn.list_books(), [])
        with self.assertRaises(BeagleError):
            self.conn.delete_book(books[0].id)                   # már nincs ott
        self.assertEqual(self.mock.errors, [])
        src.close()

    def test_resume_and_cancel(self):
        pdf = os.path.join(self.tmp.name, "konyv.pdf")
        make_pdf(pdf, pages=6)
        src = render.BookSource(pdf)

        cancel = threading.Event()
        def stop_after_two(n, total):
            if n == 2:
                cancel.set()
        self.assertEqual(upload_book(self.conn, src, stop_after_two, cancel), UploadResult.CANCELLED)
        partial = self.conn.list_books()[0]
        self.assertEqual(partial.last_page, 2)

        self.assertEqual(upload_book(self.conn, src), UploadResult.RESUMED)
        self.assertEqual(sorted(self.mock.books[src.book_id]["pages"]), list(range(7)))

        self.assertEqual(upload_book(self.conn, src), UploadResult.ALREADY_THERE)
        self.assertEqual(self.mock.errors, [])
        src.close()

    def test_single_page_document(self):
        pdf = os.path.join(self.tmp.name, "egy.pdf")
        make_pdf(pdf, pages=1)
        src = render.BookSource(pdf)
        self.assertEqual(upload_book(self.conn, src), UploadResult.UPLOADED)   # nincs 0-val osztás
        self.assertEqual(self.mock.errors, [])
        src.close()

    def test_epub_upload(self):
        epub = os.path.join(self.tmp.name, "konyv.epub")
        make_epub(epub)
        title, author, reflowable = render.peek_metadata(epub)
        self.assertTrue(reflowable)
        src = render.BookSource(epub, font_size=26)
        self.assertGreater(src.page_count, 3)
        self.assertEqual(upload_book(self.conn, src), UploadResult.UPLOADED)
        self.assertEqual(self.mock.errors, [])
        out = os.environ.get("BEAGLE_TEST_OUT")
        if out:
            os.makedirs(out, exist_ok=True)
            img = src.render_page(2)
            img.save(os.path.join(out, "epub_page2.png"))
        src.close()

    def test_disconnect_gives_clear_error(self):
        self.mock.stop()
        self.conn._sock.close()
        with self.assertRaises(BeagleError):
            self.conn.info()


class BluetoothConnectTests(unittest.TestCase):
    """A connect() újrapróbálása és hibaüzenetei (Bluetooth nélkül, hamis socketekkel)."""

    def _fake(self, errors):
        calls = []

        class FakeSock:
            def __init__(self, *a):
                pass
            def settimeout(self, t):
                pass
            def connect(self, addr):
                calls.append(addr)
                if errors:
                    raise OSError(errors.pop(0), "hiba")
            def close(self):
                pass

        return FakeSock, calls

    def _run(self, errors, attempts=3):
        from unittest import mock
        from beagle import bluetooth
        fake, calls = self._fake(list(errors))
        seen = []
        with mock.patch.object(bluetooth.socket, "socket", fake), \
                mock.patch.object(bluetooth.time, "sleep"):
            try:
                result = bluetooth.connect("74:E5:43:51:D4:FA", attempts=attempts,
                                           on_attempt=lambda n, t: seen.append(n))
                return result, None, calls, seen
            except BeagleError as exc:
                return None, exc, calls, seen

    def test_retries_host_down_then_succeeds(self):
        import errno
        conn, err, calls, seen = self._run([errno.EHOSTDOWN, errno.EHOSTDOWN])
        self.assertIsNotNone(conn)
        self.assertEqual(len(calls), 3)
        self.assertEqual(seen, [1, 2, 3])

    def test_gives_up_with_clear_message(self):
        import errno
        conn, err, calls, _ = self._run([errno.EHOSTDOWN] * 3)
        self.assertIsNone(conn)
        self.assertEqual(len(calls), 3)
        self.assertIn("Bluetooth módban", str(err))
        self.assertIn("112", str(err))

    def test_non_transient_error_not_retried(self):
        import errno
        conn, err, calls, _ = self._run([errno.EACCES])
        self.assertEqual(len(calls), 1)
        self.assertIn("párosítást", str(err))


class DocumentErrorTests(unittest.TestCase):
    def test_not_a_document(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(b"ez nem pdf")
        try:
            with self.assertRaises(render.DocumentError):
                render.BookSource(fh.name)
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
