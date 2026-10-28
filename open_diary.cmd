@echo off
rem Opens the diary in the browser with Edit / Generate buttons for the Story captions.
rem Keep this window open while editing; close it when done. Works on this PC only.
cd /d "%~dp0"
"C:\Program Files\Python312\python.exe" -m components.diary.diary serve
