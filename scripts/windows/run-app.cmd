@echo off
REM Double-click this to run Meeting Agent for a manual test.
setlocal
set "HERE=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%run-app.ps1" %*
if errorlevel 1 pause
endlocal
