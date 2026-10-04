# SPDX-License-Identifier: GPL-3.0-or-later
"""Könyv feltöltése: renderelés külön szálon, küldés a főszálon."""

from __future__ import annotations

import queue
import threading
from typing import Callable

from .protocol import BeagleConnection, BeagleError
from .render import BookSource

ProgressCallback = Callable[[int, int], None]  # (oldalszám, utolsó oldal száma)

MAX_TEXT = 80  # cím/szerző maximális hossza a készüléknek küldve


class UploadResult:
    UPLOADED = "uploaded"
    RESUMED = "resumed"
    ALREADY_THERE = "already_there"
    CANCELLED = "cancelled"


def upload_book(conn: BeagleConnection, source: BookSource,
                progress: ProgressCallback | None = None,
                cancel: threading.Event | None = None) -> str:
    """Feltölti a könyvet; ha már (részben) fent van, onnan folytatja.

    A folytatás a jBeagle logikáját követi: ha a készüléken már létezik
    ugyanilyen azonosítójú könyv, az utolsó oldaltól indul újra a küldés.
    Visszatérés: az `UploadResult` egyik értéke.
    """
    total = source.page_count          # az utolsó oldal sorszáma (a 0. a címlap)
    start = 0
    existed = False
    for book in conn.list_books():
        if book.id == source.book_id:
            existed = True
            start = min(book.last_page, total)
    already_complete = existed and start >= total

    pages: queue.Queue = queue.Queue(maxsize=3)
    stop = threading.Event()

    def producer() -> None:
        try:
            for item in source.iter_encoded(start):
                while not stop.is_set():
                    try:
                        pages.put(item, timeout=0.3)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
            pages.put(None)
        except Exception as exc:  # a fogyasztó szálban újradobjuk
            pages.put(exc)

    thread = threading.Thread(target=producer, daemon=True)
    conn.begin_book(source.book_id, source.title[:MAX_TEXT], source.author[:MAX_TEXT])
    thread.start()
    cancelled = False
    try:
        while True:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            try:
                item = pages.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise BeagleError(f"Az oldal feldolgozása nem sikerült: {item}") from item
            number, data = item
            conn.upload_page(number, data)
            if progress:
                progress(number, total)
    finally:
        stop.set()
        thread.join(timeout=5)
    conn.end_book()

    if cancelled:
        return UploadResult.CANCELLED
    if already_complete:
        return UploadResult.ALREADY_THERE
    return UploadResult.RESUMED if existed else UploadResult.UPLOADED
