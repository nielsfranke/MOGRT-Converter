"""PyInstaller entry point."""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    # worker processes (playback prefetch) end here, before anything of the app is imported
    multiprocessing.freeze_support()
    # windowed builds (Windows) have no stdout/stderr; warnings must not fail on print()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    from mogrt_converter.desktop import main

    main()
