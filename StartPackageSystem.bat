@echo off
chcp 65001 >nul
title Package System Services
cd /d "C:\\xampp\\htdocs\\package_webapp"

echo ========================================
echo    PACKAGE SYSTEM - STARTING SERVICES
echo ========================================
echo.

REM Set proper Python path
set PYTHON_PATH=C:\\Program Files\\Python312\\python.exe

REM Kill existing processes first
echo Cleaning up existing processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im httpd.exe >nul 2>&1
taskkill /f /im mysqld.exe >nul 2>&1
timeout /t 3 /nobreak >nul

REM Check if XAMPP needs to be installed
echo Checking XAMPP installation...
if not exist "C:\xampp\xampp-control.exe" (
    echo XAMPP not found. Checking for installer...
    if exist "xampp-windows-x64-8.2.12-0-VS16-installer.exe" (
        echo Please install XAMPP first using the installer in this folder.
        echo Run: xampp-windows-x64-8.2.12-0-VS16-installer.exe
        echo Then run this batch file again.
        pause
        exit /b 1
    ) else (
        echo XAMPP installer not found. Please install XAMPP manually.
        pause
        exit /b 1
    )
)

REM Start XAMPP services
echo Starting Apache...
start "Apache" /min "C:\xampp\apache\bin\httpd.exe"

echo Starting MySQL...
start "MySQL" /min "C:\xampp\mysql\bin\mysqld.exe" --defaults-file="C:\xampp\mysql\bin\my.ini"

REM Wait for database services to initialize
echo Waiting for database services (15 seconds)...
timeout /t 15 /nobreak >nul

REM Test database connection
echo Testing database connection...
"C:\\Program Files\\Python312\\python.exe" -c "import mysql.connector; conn = mysql.connector.connect(host='localhost', user='root', password='', database='clement_package_log'); print('Database connected successfully'); conn.close()" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Database connection failed. Services may not work properly.
    echo This is normal if it's the first run. The system will create the database automatically.
)

REM Start Flask WebApp with explicit Python
echo Starting Flask Web Application...
start "Flask WebApp" /min "C:\\Program Files\\Python312\\python.exe" "WebApp.py" --port 5300

REM Wait for Flask to start
echo Waiting for Flask to start (10 seconds)...
timeout /t 10 /nobreak >nul

REM Start Package Scanner
echo Starting Package Scanner...
start "Package Scanner" /min "C:\\Program Files\\Python312\\python.exe" "PackageLog_Python.py"

echo.
echo ========================================
echo      SERVICES STARTED SUCCESSFULLY!
echo ========================================
echo.
echo Checking service status...

REM Check what's running
echo [Service Status]
tasklist /fi "imagename eq httpd.exe" | find "httpd.exe" >nul && echo - Apache: RUNNING || echo - Apache: NOT RUNNING
tasklist /fi "imagename eq mysqld.exe" | find "mysqld.exe" >nul && echo - MySQL: RUNNING || echo - MySQL: NOT RUNNING
tasklist /fi "imagename eq python.exe" | find /c "python.exe" >nul && echo - Python processes: RUNNING || echo - Python processes: NOT RUNNING

echo.
echo ========================================
echo  Web Interface: http://localhost:5300
echo.
echo  All services are running in background.
echo  You can safely close this window.
echo ========================================
echo.

REM Open browser after everything is started
echo Opening web browser...
start "" "http://localhost:5300"

echo.
echo If the web page doesn't load, wait 30 seconds and refresh.
echo Or check if your firewall is blocking the connection.
echo.

pause
