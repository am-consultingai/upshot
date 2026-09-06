@echo off
REM Double-click this to run Meeting Agent.
REM
REM If Claude Code is already installed it offers to remove it first, so the Settings
REM screen starts at "Not installed" and its Install button can be tested from a clean
REM state. Pressing Enter keeps it. Pass -KeepClaude to skip the question entirely.
REM
REM The app is started in its own console rather than in this one, and this window then
REM exits immediately. That is deliberate: a batch file interrupted with Ctrl+C makes
REM cmd.exe ask "Terminate batch job (Y/N)?", and there is no way to suppress that from
REM inside the batch file. Ctrl+C means stop, not "ask me whether I meant it" - so by the
REM time the app is running, no batch file is left to prompt.
setlocal
start "Meeting Agent" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-app.ps1" %*
endlocal
