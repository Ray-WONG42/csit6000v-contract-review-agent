@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\tacc_service.py start
pause
