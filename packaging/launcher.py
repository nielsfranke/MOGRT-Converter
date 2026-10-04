"""PyInstaller entry point."""

import multiprocessing

from mogrt_converter.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
