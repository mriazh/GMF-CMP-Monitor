@echo off
setlocal
title GMF CMP Monitor - Live Dashboard

echo =====================================================================
echo                Starting GMF CMP Dashboard Monitor
echo =====================================================================
echo.

:: Check Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python tidak ditemukan di sistem!
    echo Silakan install Python dan pastikan ditambahkan ke PATH.
    echo.
    pause
    exit /b 1
)

:: Check .env file
if not exist ".env" (
    echo [ERROR] File konfigurasi .env tidak ditemukan!
    echo Jalankan setup.bat terlebih dahulu atau buat file .env dari .env.example.
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

python main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] Program berhenti dengan kode exit: %ERRORLEVEL%
    pause
)
