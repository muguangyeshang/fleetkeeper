# -*- coding: utf-8 -*-
"""PyInstaller entry point.

Double-clicking dist/fleetkeeper.exe calls main([]), which resolves to
`fleetkeeper start`: auto-create fleet.json next to the exe, serve the local
console, open the browser. No arguments, no terminal, no install step.
"""
import sys

from fleetkeeper.cli import main

if __name__ == "__main__":
    sys.exit(main())
