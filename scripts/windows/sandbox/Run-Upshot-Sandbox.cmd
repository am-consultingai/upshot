@echo off
rem Machine B: double-click to watch Upshot install, run and uninstall inside Windows Sandbox.
rem Fetches the latest test scripts from Drive every time, then hands over to
rem Start-UpshotSandbox.ps1. Nothing is installed on this machine.
setlocal
title Upshot sandbox test
set "KIT=C:\upshot-work\sandbox-watch"
set "KITWSL=/mnt/c/upshot-work/sandbox-watch"
set "DRIVE=tools/sandbox-watch"
if not exist "%KIT%" mkdir "%KIT%"
echo.
echo   Fetching the latest test scripts from Google Drive...
for %%F in (Start-UpshotSandbox.ps1 sandbox-test.ps1 show.wsb) do (
  wsl.exe -e bash -lc "python3 ~/upshot-agent/drive.py get %DRIVE%/%%F %KITWSL%/%%F" || goto :fetchfailed
  echo     %%F
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIT%\Start-UpshotSandbox.ps1"
exit /b %errorlevel%

:fetchfailed
echo.
echo   Could not fetch the test scripts from Drive. Is Ubuntu (WSL) working, and does
echo   ~/upshot-agent/drive.py exist in it?
pause
exit /b 1
