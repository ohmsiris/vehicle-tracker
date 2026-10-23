@echo off
rem The weekly backup (Windows Task Scheduler runs this Monday 11:00): scripts\backup.py, then a copy to Google Drive.
rem What happened is written to backups\last_run.log. Needs the drive this folder is on to be plugged in, and (for the copy)
rem Google Drive for Desktop running with a folder "IceBox Backup" in My Drive. See the README, "Backups".
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist backups mkdir backups
".venv\Scripts\python.exe" scripts\backup.py > backups\last_run.log 2>&1
set RC=%ERRORLEVEL%
echo exit code %RC% >> backups\last_run.log
if not "%RC%"=="0" goto :end

rem find the Drive folder: Google Drive for Desktop shows My Drive as a drive letter (it is not always the same one)
set "DEST="
for %%L in (E F H I J K L M N O P Q R S T U V W X Y Z) do if exist "%%L:\My Drive\IceBox Backup\" if not defined DEST set "DEST=%%L:\My Drive\IceBox Backup"
if not defined DEST goto :nodrive

rem the photos: only the ones Drive does not have yet; the database export: this run's folder, kept under runs\ by date
robocopy "backups\files" "%DEST%\files" /E /XO /R:2 /W:5 /NFL /NDL /NJH /NJS >> backups\last_run.log 2>&1
set "LATEST="
for /f "delims=" %%d in ('dir /b /ad /o-n backups\20*') do if not defined LATEST set "LATEST=%%d"
robocopy "backups\%LATEST%" "%DEST%\runs\%LATEST%" /E /R:2 /W:5 /NFL /NDL /NJH /NJS >> backups\last_run.log 2>&1
echo copied to Google Drive: %DEST% - this run: %LATEST% >> backups\last_run.log
goto :end

:nodrive
echo NOT COPIED TO DRIVE: no folder "IceBox Backup" found in My Drive (is Google Drive for Desktop running?) >> backups\last_run.log

:end
