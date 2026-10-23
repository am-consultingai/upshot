@echo off
rem The test machine: double-click to watch Upshot install, run and uninstall inside Windows Sandbox
rem ("Run-Upshot-Sandbox.cmd watch" to click through the installer yourself).
rem Fetches the latest test scripts from Drive every time, then hands over to
rem Start-UpshotSandbox.ps1. Nothing is installed on this machine.
rem Keep this file in a folder of its own, with .env beside it (KEY=value lines, never committed):
rem UPSHOT_SANDBOX_DRIVE_FOLDER and UPSHOT_AGENT_DIR (an absolute WSL folder). Only the kit\
rem subfolder is mapped into the sandbox; this folder and .env are not.
setlocal EnableDelayedExpansion
title Upshot sandbox test
set "HERE=%~dp0"
set "HERE=!HERE:~0,-1!"
set "MODE=show"
if /i "%~1"=="watch" set "MODE=watch"
if not exist "!HERE!\.env" goto :noenv
rem Only the two keys, from .env only: nothing else it holds, and no environment variable.
set "UPSHOT_SANDBOX_DRIVE_FOLDER="
set "UPSHOT_AGENT_DIR="
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("!HERE!\.env") do (
  if /i "%%A"=="UPSHOT_SANDBOX_DRIVE_FOLDER" set "UPSHOT_SANDBOX_DRIVE_FOLDER=%%~B"
  if /i "%%A"=="UPSHOT_AGENT_DIR" set "UPSHOT_AGENT_DIR=%%~B"
)
if "!UPSHOT_SANDBOX_DRIVE_FOLDER!"=="" goto :noenv
if "!UPSHOT_AGENT_DIR!"=="" goto :noenv
set "HEREWSL="
for /f "usebackq delims=" %%W in (`wsl.exe -e wslpath -a "!HERE!"`) do set "HEREWSL=%%W"
if "!HEREWSL!"=="" goto :fetchfailed
echo.
echo   Fetching the latest test scripts from Google Drive...
rem The launcher's own files, here. Start-UpshotSandbox.ps1 fetches what goes into kit\, after
rem checking that the sandbox has left no link there.
for %%F in (Start-UpshotSandbox.ps1 show.wsb watch.wsb) do (
  wsl.exe -e python3 "!UPSHOT_AGENT_DIR!/drive.py" get "!UPSHOT_SANDBOX_DRIVE_FOLDER!/%%F" "!HEREWSL!/%%F" || goto :fetchfailed
  echo     %%F
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "!HERE!\Start-UpshotSandbox.ps1" -Wsb !MODE!
set "CODE=!errorlevel!"
if not "!CODE!"=="0" pause
exit /b !CODE!

:noenv
echo.
echo   !HERE!\.env must set UPSHOT_SANDBOX_DRIVE_FOLDER and UPSHOT_AGENT_DIR (KEY=value lines,
echo   saved as UTF-8 without a BOM or as plain ASCII).
pause
exit /b 1

:fetchfailed
echo.
echo   Could not fetch the test scripts from Drive. Is Ubuntu (WSL) working, and does
echo   !UPSHOT_AGENT_DIR!/drive.py exist in it?
pause
exit /b 1
