@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0renew_token.ps1" %*
exit /b %ERRORLEVEL%
