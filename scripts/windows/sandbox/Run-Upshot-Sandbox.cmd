@echo off
rem The test machine: double-click to watch Upshot install, run and uninstall inside Windows Sandbox.
rem Fetches the latest test scripts from Drive every time, then hands over to
rem Start-UpshotSandbox.ps1. Nothing is installed on this machine.
rem The kit is the folder this file is in. This machine's own values come from .env beside it
rem (KEY=value lines, never committed): UPSHOT_SANDBOX_DRIVE_FOLDER and UPSHOT_AGENT_DIR.
setlocal
title Upshot sandbox test
set "KIT=%~dp0"
set "KIT=%KIT:~0,-1%"
if not exist "%KIT%\.env" goto :noenv
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%KIT%\.env") do set "%%A=%%~B"
if "%UPSHOT_SANDBOX_DRIVE_FOLDER%"=="" goto :noenv
if "%UPSHOT_AGENT_DIR%"=="" goto :noenv
for /f "usebackq delims=" %%W in (`wsl.exe -e wslpath -a "%KIT%"`) do set "KITWSL=%%W"
echo.
echo   Fetching the latest test scripts from Google Drive...
for %%F in (Start-UpshotSandbox.ps1 sandbox-test.ps1 show.wsb) do (
  wsl.exe -e bash -lc "python3 %UPSHOT_AGENT_DIR%/drive.py get %UPSHOT_SANDBOX_DRIVE_FOLDER%/%%F '%KITWSL%/%%F'" || goto :fetchfailed
  echo     %%F
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIT%\Start-UpshotSandbox.ps1"
exit /b %errorlevel%

:noenv
echo.
echo   %KIT%\.env must set UPSHOT_SANDBOX_DRIVE_FOLDER and UPSHOT_AGENT_DIR (KEY=value lines).
pause
exit /b 1

:fetchfailed
echo.
echo   Could not fetch the test scripts from Drive. Is Ubuntu (WSL) working, and does
echo   %UPSHOT_AGENT_DIR%/drive.py exist in it?
pause
exit /b 1
