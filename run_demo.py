"""
Launcher for Tmap Interactive Terminal.
Pass --mock for synthetic mock feed or --mt5 to connect to live MetaTrader 5 Bridge on port 5555.
"""
from tmap.ui.app import main

if __name__ == "__main__":
    main()
