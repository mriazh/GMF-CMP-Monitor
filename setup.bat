@echo off
setlocal
title GMF CMP Monitor - Setup & Installation

echo =====================================================================
echo           GMF CMP Monitor - Automated Setup Script
echo =====================================================================
echo.

:: Check Python installation
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python tidak ditemukan di sistem!
    echo Silakan install Python 3.10+ dari https://www.python.org/
    echo Pastikan mencentang opsi "Add python.exe to PATH" saat instalasi.
    echo.
    pause
    exit /b 1
)

echo [1/4] Memeriksa versi Python...
python --version

echo.
echo [2/4] Menginstall dependencies Python (Playwright, tzdata)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Gagal menginstall dependencies Python!
    pause
    exit /b 1
)

echo.
echo [3/4] Menginstall browser Firefox untuk Playwright...
python -m playwright install firefox
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Gagal menginstall Firefox browser driver!
    pause
    exit /b 1
)

echo.
echo [4/4] Memeriksa konfigurasi environment (.env)...
if not exist "logs" (
    mkdir logs
)

if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [INFO] File .env dibuat dari .env.example.
        echo Silakan buka dan lengkapi kredensial di file .env sebelum menjalankan monitor!
    ) else (
        echo [WARNING] File .env.example tidak ditemukan!
    )
) else (
    echo [OK] File .env sudah ada.
)

echo.
echo =====================================================================
echo               Setup Selesai dan Siap Digunakan!
echo   Jalankan start_monitor.bat untuk memulai monitoring dashboard.
echo =====================================================================
echo.
pause
