@echo off
:: ─────────────────────────────────────────────────────────────────────────────
::  Hermes Agent — Offline Edition  |  Windows One-Click Installer
::  Double-click this file OR run from Command Prompt / PowerShell.
::
::  This launcher runs install-windows.ps1 with the correct execution policy.
::  No permanent system changes to execution policy are made.
:: ─────────────────────────────────────────────────────────────────────────────
title Hermes Agent — Offline Installer

echo.
echo  =====================================================
echo    Hermes Agent -- Offline Edition - Windows Setup
echo  =====================================================
echo.
echo  This will install:
echo    - Python 3.12  (if not present)
echo    - uv           (fast package manager)
echo    - Ollama        (local LLM server)
echo    - hermes-agent  (the AI agent)
echo    - hermes-offline (offline extension)
echo.
echo  Press Ctrl+C to cancel, or...
pause

:: Run the PowerShell installer in this window with Bypass policy
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation encountered an error. Exit code: %ERRORLEVEL%
    echo  See messages above for details.
    pause
    exit /b %ERRORLEVEL%
)

exit /b 0
