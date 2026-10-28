@echo off
rem Starts the floating helper window + tray icon (no console). Safe to run twice: a second copy exits.
cd /d "%~dp0"
start "" "C:\Program Files\Python312\pythonw.exe" companion.py
