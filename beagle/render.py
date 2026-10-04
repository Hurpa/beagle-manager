# SPDX-License-Identifier: GPL-3.0-or-later
"""Dokumentumok (PDF, EPUB, ...) átalakítása Beagle-oldalakká.

Egy Beagle-oldal:
  * 600x800 képpont, 4 bit/képpont (az alsó bit mindig 0 => 8 szürkeárnyalat),
    az első képpont a bájt felső négy bitjén;
  * a nyers adat 600*800/2 bájt + 64 bájt nulla kitöltés;
  * az egész gzip-pel tömörítve, 2 KB-os ablakkal (windowBits=27), memLevel=9.

Forrás: opentxtr (imgpipe.cc, zpipe.c) és jBeagle (BeagleCompressor.java,
BeagleRenderer.java).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import zlib
from typing import Iterator

from PIL import Image, ImageDraw, ImageFont

try:
    import pymupdf
except ImportError:  # régebbi PyMuPDF
    import fitz as pymupdf  # type: ignore

PAGE_W = 600
PAGE_H = 800
CONTENT_H = 790          # alul marad hely a haladásjelzőnek
BAR_Y = 795              # haladásjelző sáv (5 képpont magas)
RAW_SIZE = PAGE_W * PAGE_H // 2 + 64

DEFAULT_FONT_SIZE = 26   # e-book (EPUB, ...) betűméret, képpontban


class DocumentError(Exception):
    """A dokumentum nem nyitható meg / nem használható."""


# Az e-bookok (EPUB, MOBI, FB2, ...) alapértelmezett, nagy margóit lecsökkentjük,
# hogy a szöveg jobban kitöltse a kis kijelzőt. A PDF-et ez nem érinti.
READER_CSS = "@page { margin: 24px } body { margin: 0; padding: 0 }"

try:
    pymupdf.mupdf.fz_set_user_css(READER_CSS)
except Exception:  # nagyon régi PyMuPDF: marad a MuPDF alapértelmezett margója
    pass


# --------------------------------------------------------------------------
# Kódolás
# --------------------------------------------------------------------------

_HI = bytes(v & 0xE0 for v in range(256))
_LO = bytes((v & 0xE0) >> 4 for v in range(256))


def pack_4bpp(gray: bytes) -> bytes:
    """8 bites szürke képpontokból 4 bites csomagolás (páronként egy bájt)."""
    hi = int.from_bytes(gray[0::2].translate(_HI), "big")
    lo = int.from_bytes(gray[1::2].translate(_LO), "big")
    return (hi | lo).to_bytes(len(gray) // 2, "big")


def encode_page(img: Image.Image) -> bytes:
    """600x800-as képből a készüléknek küldhető, gzip-elt oldaladat."""
    if img.size != (PAGE_W, PAGE_H):
        raise ValueError(f"Az oldalkép mérete {PAGE_W}x{PAGE_H} kell legyen.")
    raw = pack_4bpp(img.convert("L").tobytes()) + bytes(64)
    comp = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, 27, 9,
                            zlib.Z_DEFAULT_STRATEGY)
    return comp.compress(raw) + comp.flush()


def decode_page(gz_data: bytes) -> Image.Image:
    """Az `encode_page` inverze (teszteléshez / előnézethez)."""
    raw = zlib.decompress(gz_data, 31)
    if len(raw) != RAW_SIZE:
        raise ValueError(f"Rossz oldalméret: {len(raw)} bájt")
    pixels = bytearray(PAGE_W * PAGE_H)
    body = raw[: PAGE_W * PAGE_H // 2]
    pixels[0::2] = bytes((b & 0xF0) for b in body)
    pixels[1::2] = bytes(((b & 0x0F) << 4) for b in body)
    return Image.frombytes("L", (PAGE_W, PAGE_H), bytes(pixels))


# --------------------------------------------------------------------------
# Betűtípus és szövegtördelés a címlaphoz
# --------------------------------------------------------------------------

_FONT_PATHS = {
    True: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ],
    False: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ],
}


def load_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    paths = list(_FONT_PATHS[bold])
    try:  # fontconfig, ha van
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold" if bold else "sans"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            paths.append(out)
    except (OSError, subprocess.SubprocessError):
        pass
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # régi Pillow
        return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while font.getlength(word) > max_width and len(word) > 1:
            k = len(word)
            while k > 1 and font.getlength(word[:k]) > max_width:
                k -= 1
            lines.append(word[:k])
            word = word[k:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit_text(text: str, max_width: int, max_lines: int, start: int, minimum: int,
              bold: bool = True) -> tuple[list[str], ImageFont.ImageFont]:
    """A legnagyobb betűméret, amivel a szöveg elfér max_lines sorban."""
    for size in range(start, minimum - 1, -2):
        font = load_font(size, bold)
        lines = _wrap(text, font, max_width)
        if len(lines) <= max_lines:
            return lines, font
    font = load_font(minimum, bold)
    lines = _wrap(text, font, max_width)[:max_lines]
    while lines[-1] and font.getlength(lines[-1] + "…") > max_width:
        lines[-1] = lines[-1][:-1]
    lines[-1] += "…"
    return lines, font


def render_title_page(title: str, author: str, cover: Image.Image | None) -> Image.Image:
    """Címlap: fent a szerző, középen a borító bélyegképe, lent a cím."""
    img = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(img)

    if cover is not None:
        thumb = cover.convert("L")
        thumb.thumbnail((300, 400))
        x, y = (PAGE_W - thumb.width) // 2, (PAGE_H - thumb.height) // 2
        img.paste(thumb, (x, y))
        draw.rectangle([x - 1, y - 1, x + thumb.width, y + thumb.height], outline=128)

    max_w = PAGE_W - 40
    lines, font = _fit_text(author, max_w, 2, 30, 18, bold=False)
    y = 25
    for line in lines:
        draw.text(((PAGE_W - font.getlength(line)) / 2, y), line, font=font, fill=0)
        y += int(font.size * 1.3) if hasattr(font, "size") else 36

    lines, font = _fit_text(title, max_w, 3, 38, 20, bold=True)
    step = int(font.size * 1.3) if hasattr(font, "size") else 40
    y = PAGE_H - 25 - step * len(lines)
    for line in lines:
        draw.text(((PAGE_W - font.getlength(line)) / 2, y), line, font=font, fill=0)
        y += step
    return img


# --------------------------------------------------------------------------
# Dokumentumforrás
# --------------------------------------------------------------------------

def default_title(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.replace("_", " ").strip() or "Névtelen"


def _open(path: str):
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # pymupdf.FileDataError és társai
        raise DocumentError(f"A fájl nem nyitható meg: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise DocumentError("A dokumentum jelszóval védett.")
    return doc


def peek_metadata(path: str) -> tuple[str, str, bool]:
    """(cím, szerző, újratördelhető-e) a fájlból, a feltöltési párbeszédhez."""
    doc = _open(path)
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip() or default_title(path)
        author = (meta.get("author") or "").strip()
        return title, author, bool(doc.is_reflowable)
    finally:
        doc.close()


class BookSource:
    """Egy feltöltendő könyv: oldalak renderelése és kódolása.

    Oldalszámozás a készüléken: 0 = címlap, 1..page_count = a dokumentum oldalai.
    """

    def __init__(self, path: str, title: str | None = None, author: str | None = None,
                 font_size: int = DEFAULT_FONT_SIZE):
        self.path = path
        self.font_size = font_size
        self.doc = _open(path)
        self.reflowable = bool(self.doc.is_reflowable)
        if self.reflowable:
            self.doc.layout(width=PAGE_W, height=CONTENT_H, fontsize=font_size)

        meta = self.doc.metadata or {}
        self.title = (title or meta.get("title") or "").strip() or default_title(path)
        self.author = (author if author is not None else (meta.get("author") or "")).strip()
        if not self.author:
            self.author = "Ismeretlen szerző"

        self.page_count = self.doc.page_count
        if self.page_count < 1:
            raise DocumentError("A dokumentumban nincs egyetlen oldal sem.")
        try:
            self.bookmarks = [p for _lvl, _t, p in self.doc.get_toc(simple=True) if p > 0]
        except Exception:
            self.bookmarks = []
        self.book_id = self._make_id()

    def close(self) -> None:
        self.doc.close()

    def _make_id(self) -> str:
        """Stabil 16 hexa jegyű azonosító a fájl tartalmából (a folytatáshoz)."""
        h = hashlib.sha1()
        with open(self.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if self.reflowable:
            h.update(f"|fontsize={self.font_size}".encode())
        return h.hexdigest()[:16].upper()

    # ---------------------------------------------------------------- renderelés

    def _render_doc_page(self, index: int) -> Image.Image:
        """A dokumentum `index`-edik (0-tól) oldala fehér, 600x CONTENT_H vásznon."""
        page = self.doc.load_page(index)
        rect = page.rect
        scale = min(PAGE_W / rect.width, CONTENT_H / rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                              colorspace=pymupdf.csGRAY, alpha=False)
        shot = Image.frombuffer("L", (pix.width, pix.height), bytes(pix.samples),
                                "raw", "L", pix.stride, 1)
        return shot

    def render_page(self, number: int) -> Image.Image:
        """Kész, 600x800-as készülékoldal (0 = címlap)."""
        if number == 0:
            cover = self._render_doc_page(0)
            return render_title_page(self.title, self.author, cover)

        index = number - 1
        shot = self._render_doc_page(index)
        canvas = Image.new("L", (PAGE_W, PAGE_H), 255)
        canvas.paste(shot, ((PAGE_W - shot.width) // 2, (CONTENT_H - shot.height) // 2))
        self._draw_progress(canvas, index)
        return canvas

    def _draw_progress(self, canvas: Image.Image, index: int) -> None:
        draw = ImageDraw.Draw(canvas)
        denom = max(self.page_count - 1, 1)
        draw.rectangle([0, BAR_Y, PAGE_W * index // denom, BAR_Y + 4], fill=128)
        for page_nr in self.bookmarks:
            x = min(PAGE_W * (page_nr - 1) // denom, PAGE_W - 1)
            draw.line([x, BAR_Y + 2, x, BAR_Y + 4], fill=0)

    def iter_encoded(self, start: int = 0) -> Iterator[tuple[int, bytes]]:
        """(oldalszám, gzip-elt oldaladat) a `start` oldaltól az utolsóig."""
        for number in range(start, self.page_count + 1):
            yield number, encode_page(self.render_page(number))
