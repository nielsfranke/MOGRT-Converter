#!/bin/sh
# Build the Linux app inside a Debian container (run from the repo root):
#   docker run --rm -v "$PWD":/src -w /src python:3.12-bookworm sh packaging/linux/build-in-docker.sh
set -e
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  libegl1 libgl1 libfontconfig1 \
  libgirepository1.0-dev libcairo2-dev pkg-config gir1.2-webkit2-4.1 gir1.2-gtk-3.0 >/dev/null
python -m venv /tmp/venv
. /tmp/venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[build]" "PyGObject<3.51"
python packaging/build.py
