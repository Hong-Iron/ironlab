@echo off
rem ironlab Windows installer launcher.
rem Double-click to install with defaults (asks for the admin name).
rem For options, run install.ps1 from an administrator PowerShell instead:
rem   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Admin admin -SiteName "My Lab GPU"
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
