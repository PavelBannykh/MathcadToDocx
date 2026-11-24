@echo off
setlocal
cd /d "%~dp0"
python convert.py --gui
if errorlevel 1 pause
