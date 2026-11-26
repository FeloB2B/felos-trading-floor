@echo off
cd /d "%~dp0"
py bridge.py
if errorlevel 1 echo Could not start. Check Python and the project README.md.
pause
