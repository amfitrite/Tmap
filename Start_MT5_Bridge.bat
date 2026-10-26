@echo off
title Tmap - Live MT5 Bridge (Port 5555)
cd /d "%~dp0"
echo ========================================================
echo  Starting Tmap Connected to MetaTrader 5 Bridge (:5555)
echo ========================================================
echo  Make sure TmapBridge.mq5 / .ex5 is attached to chart in MT5!
echo.
python run_mt5.py
pause
