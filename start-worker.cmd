@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" worker.py serve
if errorlevel 1 pause
