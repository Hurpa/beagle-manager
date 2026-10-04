#!/usr/bin/env bash
cd "$(dirname "$(readlink -f "$0")")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python beagle_manager.py "$@"
fi
exec python3 beagle_manager.py "$@"
