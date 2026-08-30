@echo off
REM Double-click this. It runs test-recording.ps1 without touching your execution policy.
setlocal
set "HERE=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%test-recording.ps1" %*
if errorlevel 1 pause
endlocal
