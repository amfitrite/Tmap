@echo off
title Tmap - Live Liquidity Heatmap (:5555)
cd /d "%~dp0"
echo ========================================================
echo  Starting Tmap Live Liquidity Heatmap (:5555)
echo ========================================================
echo.
python demo_interactive.py --mt5
pause
