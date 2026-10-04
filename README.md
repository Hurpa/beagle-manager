# Beagle kezelő

Egyszerű Python/tkinter program a **txtr Beagle** e-könyvolvasó kezeléséhez Linux alatt
(Linux Mint / Ubuntu). A régi jBeagle (Java) Linuxon is működő, Python-alapú utódja.

- Könyvek **listázása** a készüléken
- Könyvek **feltöltése**: PDF, valamint EPUB, MOBI, FB2, CBZ, XPS, TXT (Calibre-es átalakítás nélkül)
- Könyvek **törlése**
- Készülékinfó és tárhely-állapot

## Telepítés

```bash
./install.sh
```

Ez telepíti a szükséges rendszercsomagokat (`python3-tk`, `python3-pil`, `python3-venv`, `bluez`),
létrehoz egy `.venv` környezetet (PyMuPDF-fel), és felvesz egy „Beagle kezelő” indítót az
alkalmazásmenübe.

Kézzel, ha úgy jobb:

```bash
sudo apt install python3-tk python3-pil python3-venv bluez
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

## Használat

1. **Párosítsd** a Beagle-t a gépeddel a rendszer Bluetooth-beállításaiban (egyszer elég).
2. Kapcsold be a Beagle-t, majd **tartsd nyomva a bekapcsoló gombot, amíg kéken villog**
   (Bluetooth mód).
3. Indítsd a programot: `./run.sh` (vagy a menüből).
4. Válaszd ki a Beagle-t a listából → **Csatlakozás**.
5. **Feltöltés…**: válassz egy vagy több fájlt. Minden fájlnál szerkesztheted a címet és a
   szerzőt (e-bookoknál a betűméretet is). Feltöltés közben a **Megszakítás** gombbal leállíthatod.
6. **Törlés**: jelöld ki a könyvet, majd kattints a gombra (vagy Delete billentyű).

Ha a feltöltés megszakad (vagy megszakítod), ugyanazt a fájlt újra feltöltve a program onnan
folytatja, ahol abbamaradt.

## Hogyan működik

A Beagle Bluetooth RFCOMM soros kapcsolaton, szöveges parancsokkal kezelhető (`INFO`,
`GETBOOKS`, `BOOK`, `PAGE n`, `ENDBOOK`, `DELETEBOOK` …). A könyvet **képekként** fogadja:
a program minden oldalt 600×800 képpontra renderel, 4 bites szürkeárnyalatossá alakít
(8 árnyalat), és gzip-pel tömörítve küldi el. A 0. oldal egy generált címlap; minden oldal alján
vékony haladásjelző sáv van (a fejezetek kis jelekkel), a jBeagle-hez hasonlóan.

| Modul | Feladata | Forrás |
|---|---|---|
| `beagle/protocol.py` | parancsok, válaszok, könyvlista | jBeagle `BeagleConnector`, opentxtr jegyzetek |
| `beagle/render.py` | oldalrenderelés, 4 bites csomagolás, gzip, címlap | jBeagle `BeagleRenderer/Compressor`, opentxtr `imgpipe`+`zpipe` |
| `beagle/bluetooth.py` | párosított eszközök keresése, RFCOMM kapcsolat | jBeagle `BeagleUtil` |
| `beagle/transfer.py` | feltöltés, folytatás, megszakítás | jBeagle `BeagleUtil.uploadPDF` |
| `beagle/gui.py` | felület | – |

A Bluetooth-hoz nincs szükség külső könyvtárra, a Python beépített `socket` modulja elég.

## Hibaelhárítás

- **„Nem sikerült csatlakozni” / `[Errno 112] Host is down`**: a gép nem éri el a készüléket.
  A Beagle a csatlakozás pillanatában legyen Bluetooth módban (kéken villog; bekapcsolás után
  tartsd nyomva a bekapcsoló gombot), 1-2 méteren belül. A program 3-szor próbálkozik; ha nem
  sikerül, kapcsold újra Bluetooth módba, és kattints rögtön a Csatlakozásra.
  Ha így sem megy, futtasd a diagnosztikát (a Beagle közben maradjon Bluetooth módban):

  ```bash
  python3 diagnose.py            # vagy: python3 diagnose.py 74:E5:43:51:D4:FA
  ```

  Megmutatja, melyik lépésnél akad el (adapter, párosítás, soros csatorna, protokoll).
  Hasznos kézi próba: `bluetoothctl connect 74:E5:43:51:D4:FA`; ha ez sem tud kapcsolatot
  építeni, a gond a rádiós kapcsolatnál van, nem a programnál. Régi vagy elromlott párosítás
  esetén: `bluetoothctl remove <cím>`, majd párosítsd újra, miközben a Beagle Bluetooth módban van.
- **Nem jelenik meg a lista**: ellenőrizd, hogy párosítva van-e: `bluetoothctl devices Paired`.
  A Bluetooth-címet kézzel is beírhatod a mezőbe (pl. `74:E5:43:51:D4:FA`).
- **Kapcsolat feltöltés közben megszakadt**: csatlakozz újra, és töltsd fel ugyanazt a fájlt –
  folytatódik.
- **E-book betűmérete**: a feltöltési párbeszédben állítható (alapérték 26); nagyobb szám = nagyobb betű.
  Más betűméret = új feltöltés (új azonosító).

## Tesztelés

A tesztek egy szoftveres „Beagle”-t használnak (`tests/mock_beagle.py`), amely a protokolljegyzetek
alapján válaszol, és a valódi készülékhez hasonlóan a gzip folyam végéből keretezi az oldalakat:

```bash
python3 -m unittest discover -s tests -v                       # protokoll, kódolás, renderelés
xvfb-run -a python3 tests/gui_smoke.py                         # GUI végigpróbálása (xvfb csomag kell)
```

> **Megjegyzés:** a tesztek mock eszközzel készültek, valódi Beagle-lel nem volt módom kipróbálni.

## Licenc

GPL-3.0-or-later. A program a [jBeagle](https://github.com/schierla/jbeagle) (Andreas Schierl, GPLv3)
és az [opentxtr](https://github.com/floe/opentxtr) (Florian Echtler, GPLv3) protokoll-leírására és
képformátum-elemzésére épül.
