# Third-party software

MOGRT Converter bundles the following programs. They run as separate executables
that the converter calls on the command line; their licenses apply to them alone.

## Ghostscript

- Used for: rasterizing EPS footage inside templates
- Copyright © Artifex Software, Inc.
- License: GNU Affero General Public License v3.0 (license text in `ghostscript/` next to the binary)
- Source code: https://github.com/ArtifexSoftware/ghostpdl-downloads/releases
  (macOS/Linux builds are made from the unmodified `ghostscript-<version>.tar.gz` with
  `packaging/ghostscript/build_gs.sh`; Windows builds are the official Artifex binaries)

## FFmpeg

- Used for: writing the rendered video files
- Provided by the imageio-ffmpeg package (https://github.com/imageio/imageio-ffmpeg)
- License: GNU General Public License v3 (build includes libx264)
- Source code: https://ffmpeg.org/download.html

## Fonts

- Montserrat (https://github.com/JulietaUla/Montserrat): SIL Open Font License 1.1
- Source Sans Pro (https://github.com/adobe-fonts/source-sans): SIL Open Font License 1.1

## Python libraries

Skia (skia-python, BSD-3), py-aep (MIT), QuickJS (MIT), HarfBuzz/uharfbuzz (MIT/Apache-2.0),
fontTools (MIT), pypdfium2/PDFium (Apache-2.0/BSD-3), esprima (BSD-2), pywebview (BSD-3),
NumPy (BSD-3), Pillow (MIT-CMU).
