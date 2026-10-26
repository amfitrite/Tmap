@echo off
title Tmap - Interactive Demo (Mock Feed)
cd /d "%~dp0"
echo ========================================================
echo  Starting Tmap Interactive Demo (Mock Synthetic Feed)
echo ========================================================
echo.
python run_demo.py --mock
pause
