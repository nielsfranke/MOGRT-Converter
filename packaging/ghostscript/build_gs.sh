#!/bin/sh
# Build a slim, self-contained Ghostscript (only used to rasterize EPS footage).
#
#   sh packaging/ghostscript/build_gs.sh            # -> build/ghostscript/<os>-<arch>/gs
#
# Uses the third-party libraries shipped in the Ghostscript source tarball (statically
# linked) and compiles the PostScript init files into the binary, so the result is a
# single executable without external resources. OCR, X11, CUPS etc. are disabled.
#
# Ghostscript is licensed under the AGPL-3.0; the license text is copied next to the
# binary and the source URL is recorded in SOURCE.txt.
set -e

GS_VERSION="${GS_VERSION:-10.08.0}"
GS_TAG="gs$(echo "$GS_VERSION" | tr -d .)"
URL="https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/${GS_TAG}/ghostscript-${GS_VERSION}.tar.gz"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
OUT="$ROOT/build/ghostscript/${OS}-${ARCH}"
# autoconf cannot handle spaces in paths -> build in a temp dir
WORK="${GS_WORKDIR:-/tmp/mogrt-ghostscript-build}"

if [ -x "$OUT/gs" ] && "$OUT/gs" --version 2>/dev/null | grep -q "^${GS_VERSION%.0}"; then
  echo "Ghostscript $GS_VERSION bereits gebaut: $OUT/gs"
  exit 0
fi

mkdir -p "$WORK" "$OUT"
cd "$WORK"
if [ ! -f "ghostscript-${GS_VERSION}.tar.gz" ]; then
  echo "Lade $URL"
  curl -fsSL -o "ghostscript-${GS_VERSION}.tar.gz" "$URL"
fi
rm -rf "ghostscript-${GS_VERSION}"
tar -xzf "ghostscript-${GS_VERSION}.tar.gz"
cd "ghostscript-${GS_VERSION}"

# never pick up system copies of the bundled libraries (keeps the binary self-contained)
export PKG_CONFIG_PATH=/nonexistent PKG_CONFIG_LIBDIR=/nonexistent
if [ "$OS" = "darwin" ]; then
  export MACOSX_DEPLOYMENT_TARGET=11.0
fi

./configure \
  --disable-cups --disable-dbus --disable-gtk --disable-fontconfig \
  --without-x --without-tesseract --without-libpaper --without-libidn --without-libtiff \
  --without-ijs --without-pdftoraster --without-urf --without-versioned-path \
  --with-drivers=PNG,PS \
  > "$WORK/configure.log" 2>&1

JOBS="$( (sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4) )"
make -j"$JOBS" gs > "$WORK/make.log" 2>&1

cp bin/gs "$OUT/gs"
strip "$OUT/gs" 2>/dev/null || true
cp LICENSE "$OUT/LICENSE.txt" 2>/dev/null || true
[ -f doc/COPYING ] && cp doc/COPYING "$OUT/COPYING.txt"
cat > "$OUT/SOURCE.txt" <<EOF
Ghostscript $GS_VERSION (Artifex Software), licensed under the GNU AGPL-3.0.
Unmodified source code: $URL
Built with: --without-tesseract --without-x --disable-cups --disable-fontconfig (see packaging/ghostscript/build_gs.sh)
EOF

echo "Fertig: $OUT/gs"
"$OUT/gs" --version
