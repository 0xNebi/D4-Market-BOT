@echo off

taskkill /f /im chrome.exe 2>nul
taskkill /f /im pythonw.exe 2>nul
timeout /t 2 /nobreak >nul

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."

set "SESSION_DIR=%PROJECT_ROOT%\DATA\sessions\SESSION_NAME_HERE"

powershell -Command "& 'C:\Program Files\Google\Chrome\Application\chrome.exe' --user-data-dir='%SESSION_DIR%' 'https://diablo.trade'"

timeout /t 5 /nobreak >nul

exit