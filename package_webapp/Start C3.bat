@echo off
    chcp 65001 >nul
    cd /d "C:\C3 - Checkin Checkout Center"

    echo ========================================
    echo    C3 - Checkin Checkout Center
    echo ========================================
    echo.

    REM Clean up any existing processes
    echo Cleaning up existing processes...
    taskkill /f /im python.exe >nul 2>&1
    taskkill /f /im httpd.exe >nul 2>&1
    taskkill /f /im mysqld.exe >nul 2>&1
    timeout /t 2 /nobreak >nul

    REM Start Apache directly in background
    echo Starting Apache...
    start /B "" "C:\xampp\apache\bin\httpd.exe"
    timeout /t 3 /nobreak >nul

    REM Start MySQL directly in background
    echo Starting MySQL...
    start /B "" "C:\xampp\mysql\bin\mysqld.exe" "--defaults-file=C:\xampp\mysql\bin\my.ini"
    timeout /t 5 /nobreak >nul

    REM Wait for services
    echo Waiting for services to start...
    timeout /t 5 /nobreak >nul

    REM Use the pre-determined port
    echo Using port 5300 for web server...

    REM Start Flask web server in HIDDEN window
    echo Starting Flask web server (hidden)...
    start "Flask Web Server" /min python WebApp.py
    timeout /t 3 /nobreak >nul

    REM Start package scanner in HIDDEN window  
    echo Starting package scanner (hidden)...
    start "Package Scanner" /min python PackageLog_Python.py
    timeout /t 2 /nobreak >nul

    echo Opening web browser...
    start "" "http://localhost:5300"

    echo.
    echo ========================================
    echo      SERVICES STARTED SUCCESSFULLY!
    echo.
    echo  Web Interface: http://localhost:5300
    echo.
    echo  All services are running in background.
    echo  You can safely close this window.
    echo ========================================
    echo.
    pause
    