#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Beagle kezelő – txtr Beagle e-könyvolvasó kezelése Linux alól.

Indítás:  python3 beagle_manager.py
"""

import sys


def _missing(what: str, hint: str) -> None:
    sys.exit(f"Hiányzik: {what}\n{hint}")


try:
    import tkinter  # noqa: F401
except ImportError:
    _missing("tkinter", "Telepítés Linux Mint / Ubuntu alatt:  sudo apt install python3-tk")

try:
    import PIL  # noqa: F401
except ImportError:
    _missing("Pillow", "Telepítés:  sudo apt install python3-pil   (vagy: pip install pillow)")

try:
    import pymupdf  # noqa: F401
except ImportError:
    try:
        import fitz  # noqa: F401
    except ImportError:
        _missing("PyMuPDF", "Telepítés:  pip install pymupdf   (lásd README.md)")

from beagle.gui import main

if __name__ == "__main__":
    main()
