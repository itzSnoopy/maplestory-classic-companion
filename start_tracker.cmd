@echo off
rem Starts the Maple EXP tracker in the background (no window). Safe to run twice: a second copy exits.
cd /d "%~dp0"
start "" "C:\Program Files\Python312\pythonw.exe" tracker.py --service
