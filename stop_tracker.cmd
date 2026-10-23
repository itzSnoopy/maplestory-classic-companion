@echo off
rem Asks the background tracker to stop cleanly (it finishes the session summary first).
type nul > "%~dp0stop"
echo Stop requested. The tracker exits within a few seconds.
