"""
Quick launcher for Tmap connected directly to live MetaTrader 5 Bridge on port 5555.
"""
import sys
from tmap.ui.app import main

if __name__ == "__main__":
    # Ensure sys.argv doesn't contain --mock so it defaults to live MT5 bridge on port 5555
    sys.exit(main())
