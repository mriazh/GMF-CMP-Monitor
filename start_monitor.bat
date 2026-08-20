@echo off
setlocal
title GMF CMP Monitor - Live Dashboard

echo =====================================================================
echo                Starting GMF CMP Dashboard Monitor
echo =====================================================================
echo.

:: Check .env file
if not exist ".env" (
    echo [ERROR] File konfigurasi .env tidak ditemukan!
    if exist ".env.example" (
        echo Silakan salin .env.example menjadi .env dan lengkapi kredensial.
    )
    echo.
    pause
    exit /b 1
)

:: Ensure logs directory exists
if not exist "logs" (
    mkdir logs
)

echo Memulai monitoring CMP Dashboard...
echo Jendela browser Firefox akan terbuka untuk menampilkan dashboard.
echo Tekan Ctrl + C di jendela ini kapan saja untuk menghentikan monitoring.
echo.

:: Check for compiled standalone executable
if exist "GMF-CMP-Monitor.exe" (
    GMF-CMP-Monitor.exe %*
    goto :after_run
)

:: Fallback: Check Python when running from source
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] GMF-CMP-Monitor.exe maupun Python tidak ditemukan di sistem!
    echo Silakan jalankan executable GMF-CMP-Monitor.exe atau install Python 3.10+.
    echo.
    pause
    exit /b 1
)

python main.py %*

:after_run
if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] Program berhenti dengan kode exit: %ERRORLEVEL%
    pause
)
