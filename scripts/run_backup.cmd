@echo off
rem The weekly backup (Windows Task Scheduler runs this Monday 11:00): see scripts\backup.py and the README, "Backups".
rem The result of the last run is in backups\last_run.log. It needs the drive this folder is on to be plugged in.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist backups mkdir backups
".venv\Scripts\python.exe" scripts\backup.py > backups\last_run.log 2>&1
echo exit code %ERRORLEVEL% >> backups\last_run.log
