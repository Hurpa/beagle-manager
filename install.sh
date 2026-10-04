#!/usr/bin/env bash
# Beagle kezelő – telepítés Linux Mint / Ubuntu alatt
set -e
cd "$(dirname "$(readlink -f "$0")")"

echo "==> Rendszercsomagok telepítése (sudo jelszót kérhet)"
sudo apt-get install -y python3-tk python3-venv python3-pil bluez

echo "==> Python-környezet létrehozása (.venv)"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

echo "==> Indító létrehozása az alkalmazásmenübe"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/beagle-manager.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Beagle kezelő
Comment=txtr Beagle e-könyvolvasó kezelése
Exec=$PWD/.venv/bin/python $PWD/beagle_manager.py
Path=$PWD
Terminal=false
Categories=Utility;
DESKTOP

echo
echo "Kész. Indítás:  ./run.sh   (vagy a menüből: „Beagle kezelő”)"
