"""PyInstaller entry point. Equivalent to ``python -m retrods``."""
import sys

from retrods.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
