#!/bin/sh
set -eu
python scripts/check_running_api.py
python -m unittest discover -s tests -v
