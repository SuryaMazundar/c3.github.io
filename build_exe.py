import os
import sys
import shutil
import glob
import subprocess
import time
import webbrowser
import threading
import requests
import psutil
from pathlib import Path

def install_required_packages():
    """Install all required packages from requirements.txt"""
    current_dir = Path(__file__).parent
    
    # Install PyInstaller if needed
    try:
        import PyInstaller
        print("PyInstaller is available")
    except ImportError:
        print("Installing PyInstaller...")
        os.system(f'"{sys.executable}" -m pip install pyinstaller')
    
    # Install psutil if not available
    try:
        import psutil
        print("psutil is available")
    except ImportError:
        print("Installing psutil...")
        os.system(f'"{sys.executable}" -m pip install psutil')
    
    # Read requirements from requirements.txt
    requirements_file = current_dir / 'requirements.txt'
    if requirements_file.exists():
        print("Installing packages from requirements.txt...")
        with open(requirements_file, 'r') as f:
            required_packages = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        for package in required_packages:
            package_name = package.split('==')[0]
            
            # Skip import check for problematic packages
            problematic_packages = ['eventlet']
            if package_name in problematic_packages:
                print(f"✓ {package} (skipping import check due to compatibility)")
                continue
                
            try:
                import_name = package_name.replace('-', '_').replace('.', '_')
                __import__(import_name)
                print(f"✓ {package} is available")
            except ImportError:
                print(f"Installing {package}...")
                result = os.system(f'"{sys.executable}" -m pip install {package}')
                if result != 0:
                    print(f"Warning: Failed to install {package}, continuing...")
            except Exception as e:
                print(f"Note: {package} has compatibility issue but will be included: {e}")
    else:
        print("requirements.txt not found, installing default packages...")
        # Fallback to default packages
        default_packages = [
            'psutil', 'flask', 'flask-socketio', 'mysql-connector-python', 
            'pandas', 'openpyxl', 'keyboard', 'pywin32', 'eventlet==0.33.3',
            'requests', 'python-dotenv', 'flask-cors', 'winshell'
        ]
        for package in default_packages:
            package_name = package.split('==')[0]
            
            # Skip import check for problematic packages
            problematic_packages = ['eventlet']
            if package_name in problematic_packages:
                print(f"✓ {package} (skipping import check due to compatibility)")
                continue
                
            try:
                import_name = package_name.replace('-', '_').replace('.', '_')
                __import__(import_name)
                print(f"✓ {package} is available")
            except ImportError:
                print(f"Installing {package}...")
                os.system(f'"{sys.executable}" -m pip install {package}')

def start_services_via_batch():
    """Start services using the batch file approach (most reliable)"""
    print("\n" + "="*50)
    print("STARTING SERVICES VIA BATCH FILE")
    print("="*50)
    
    current_dir = Path(__file__).parent
    
    # Look for the batch file
    batch_files = [
        current_dir / "Start C3.bat",
        current_dir / "StartPackageSystem.bat"
    ]
    
    batch_file = None
    for bf in batch_files:
        if bf.exists():
            batch_file = bf
            break
    
    if not batch_file:
        print("No batch file found. Creating one...")
        batch_file = create_startup_batch(current_dir)
    
    if batch_file and batch_file.exists():
        print(f"Starting services via: {batch_file.name}")
        
        try:
            # Use START to run the batch file in a new window that stays open
            subprocess.Popen(f'start "Package System Services" /min cmd /c "{batch_file}"', 
                           shell=True)
            print("✓ Services started via batch file")
            return True
        except Exception as e:
            print(f"✗ Failed to start batch file: {e}")
            return False
    else:
        print("✗ No batch file available")
        return False

def create_startup_batch(current_dir):
    """Create a reliable startup batch file"""
    batch_file = current_dir / "StartPackageSystem.bat"
    
    # Get absolute paths
    current_dir_str = str(current_dir).replace('\\', '\\\\')
    python_exe = sys.executable.replace('\\', '\\\\')
    
    with open(batch_file, 'w') as f:
        f.write(f"""@echo off
chcp 65001 >nul
title Package System Services
cd /d "{current_dir_str}"

echo ========================================
echo    PACKAGE SYSTEM - STARTING SERVICES
echo ========================================
echo.

REM Set proper Python path
set PYTHON_PATH={python_exe}

REM Kill existing processes first
echo Cleaning up existing processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im httpd.exe >nul 2>&1
taskkill /f /im mysqld.exe >nul 2>&1
timeout /t 3 /nobreak >nul

REM Check if XAMPP needs to be installed
echo Checking XAMPP installation...
if not exist "C:\\xampp\\xampp-control.exe" (
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
start "Apache" /min "C:\\xampp\\apache\\bin\\httpd.exe"

echo Starting MySQL...
start "MySQL" /min "C:\\xampp\\mysql\\bin\\mysqld.exe" --defaults-file="C:\\xampp\\mysql\\bin\\my.ini"

REM Wait for database services to initialize
echo Waiting for database services (15 seconds)...
timeout /t 15 /nobreak >nul

REM Test database connection
echo Testing database connection...
"{python_exe}" -c "import mysql.connector; conn = mysql.connector.connect(host='localhost', user='root', password='', database='clement_package_log'); print('Database connected successfully'); conn.close()" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Database connection failed. Services may not work properly.
    echo This is normal if it's the first run. The system will create the database automatically.
)

REM Start Flask WebApp with explicit Python
echo Starting Flask Web Application...
start "Flask WebApp" /min "{python_exe}" "WebApp.py" --port 5300

REM Wait for Flask to start
echo Waiting for Flask to start (10 seconds)...
timeout /t 10 /nobreak >nul

REM Start Package Scanner
echo Starting Package Scanner...
start "Package Scanner" /min "{python_exe}" "PackageLog_Python.py"

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
""")
    
    print(f"✓ Created reliable batch file: {batch_file}")
    return batch_file

def wait_for_server(url, timeout=60):
    """Wait for the server to be ready with longer timeout"""
    print(f"Waiting for server at {url} (timeout: {timeout}s)...")
    start_time = time.time()
    attempts = 0
    
    while time.time() - start_time < timeout:
        attempts += 1
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"✓ Server is ready after {attempts} attempts!")
                return True
        except requests.RequestException as e:
            if attempts % 5 == 0:  # Print status every 5 attempts
                print(f"  Attempt {attempts}: {e}")
            pass
        time.sleep(3)  # Wait longer between attempts
    
    print(f"✗ Server not ready after {timeout} seconds")
    return False

def open_browser(url):
    """Open browser when server is ready"""
    if wait_for_server(url, timeout=90):  # Longer timeout for services to start
        print(f"✓ Opening browser to {url}")
        webbrowser.open(url)
    else:
        print(f"✗ Could not connect to {url}")
        print("You can try manually opening the browser later.")

def check_services_status():
    """Check which services are actually running"""
    print("\n" + "="*50)
    print("CHECKING SERVICE STATUS")
    print("="*50)
    
    services = {
        'Apache': ['httpd.exe', 'apache.exe'],
        'MySQL': ['mysqld.exe', 'mysql.exe'],
        'Flask WebApp': ['python.exe'],
        'Package Scanner': ['python.exe']
    }
    
    running_services = []
    
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            proc_name = proc.info['name'].lower() if proc.info['name'] else ''
            cmdline = proc.info.get('cmdline', [])
            
            # Check for Apache
            if any(apache_proc in proc_name for apache_proc in services['Apache']):
                running_services.append('Apache')
            
            # Check for MySQL
            if any(mysql_proc in proc_name for mysql_proc in services['MySQL']):
                running_services.append('MySQL')
            
            # Check for Flask WebApp
            if 'python.exe' in proc_name and cmdline:
                cmd_str = ' '.join(cmdline).lower()
                if 'webapp.py' in cmd_str:
                    running_services.append('Flask WebApp')
            
            # Check for Package Scanner
            if 'python.exe' in proc_name and cmdline:
                cmd_str = ' '.join(cmdline).lower()
                if 'packagelog_python.py' in cmd_str:
                    running_services.append('Package Scanner')
                    
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    # Remove duplicates and print status
    running_services = list(set(running_services))
    
    for service in services.keys():
        status = "✓ RUNNING" if service in running_services else "✗ NOT RUNNING"
        print(f"{service}: {status}")
    
    return running_services

def launch_application():
    """Launch all services and applications using batch file approach"""
    print("\n" + "="*60)
    print("LAUNCHING COMPLETE PACKAGE SYSTEM")
    print("="*60)
    
    current_dir = Path(__file__).parent
    
    # Create a reliable batch file first
    print("Creating reliable startup batch file...")
    batch_file = create_startup_batch(current_dir)
    
    # Step 1: Start services via batch file
    print("\nStarting services via batch file...")
    try:
        # Run the batch file directly (not with start) so we can see if it fails
        result = subprocess.run(f'cmd /c "{batch_file}"', 
                              shell=True, 
                              timeout=30,  # Wait 30 seconds to see if it starts
                              capture_output=True, 
                              text=True)
        
        if result.returncode != 0:
            print(f"Batch file exited with code: {result.returncode}")
            print("This usually means XAMPP needs to be installed first.")
            
    except subprocess.TimeoutExpired:
        print("Batch file is running (timeout expired - this is good)")
    except Exception as e:
        print(f"Error running batch file: {e}")
    
    # Step 2: Wait and check service status
    print("\nWaiting for services to initialize...")
    time.sleep(10)
    
    running_services = check_services_status()
    
    # Step 3: Provide clear instructions
    print("\n" + "="*60)
    print("NEXT STEPS")
    print("="*60)
    
    if not running_services:
        print("\nSERVICES DID NOT START AUTOMATICALLY")
        print("\nThis is usually because:")
        print("1. XAMPP is not installed")
        print("2. Python is not properly configured")
        print("3. Required databases are not set up")
        print("\nMANUAL SETUP REQUIRED:")
        print(f"1. Install XAMPP from: {current_dir / 'xampp-windows-x64-8.2.12-0-VS16-installer.exe'}")
        print("2. Run the C3.py installer to set up the database")
        print("3. Then run StartPackageSystem.bat manually")
    else:
        print("\nSome services are running")
        print("The system should be available at: http://localhost:5300")
    
    print(f"\nYou can always run manually: {batch_file}")
    print("Or run the C3.py installer first to set up everything")
    
    # Open browser anyway
    url = "http://localhost:5300"
    print(f"\nAttempting to open browser to: {url}")
    webbrowser.open(url)

def build_executable_complete():
    """Build the executable"""
    current_dir = Path(__file__).parent
    
    install_required_packages()
    os.chdir(current_dir)
    
    print("Building executable with all required files...")
    
    # ADD THIS: Kill any existing processes and remove old executable
    kill_existing_processes(current_dir)
    
    # Use sys.executable to call pyinstaller as a module - works even when
    # Python's Scripts folder is not in PATH (common on Windows)
    pyinstaller_cmd = f'"{sys.executable}" -m PyInstaller'
    
    cmd_parts = [
        pyinstaller_cmd,
        '--onefile',
        '--console',
        '--name PackageSystemInstaller',
        '--clean',
    ]
    
    if (current_dir / 'package_icon.ico').exists():
        cmd_parts.append('--icon=package_icon.ico')
    
    items_to_include = [
        'pythonInstallations', 'Templates', 'nssm-2.24 (1)',
        'C3.py', 'log_error.txt', 'log_output.txt', 'OAINITIALS.csv',
        'PackageLog_Python.py', 'WebApp.py', 
        'xampp-windows-x64-8.2.12-0-VS16-installer.exe',
        'clement_package_log.sql'
    ]
    
    for item_name in items_to_include:
        item_path = current_dir / item_name
        if item_path.exists():
            if item_path.is_dir():
                cmd_parts.append(f'--add-data "{item_name}{os.pathsep}{item_name}"')
                print(f"✓ {item_name}/")
            else:
                cmd_parts.append(f'--add-data "{item_name}{os.pathsep}."')
                print(f"✓ {item_name}")
    
    hidden_imports = [
        'psutil', 'psutil._psutil_windows', 'psutil._psutil_common',
        'flask', 'flask_socketio', 'mysql.connector', 'pandas', 'openpyxl',
        'keyboard', 'win32gui', 'win32con', 'win32api', 'win32process',
        'requests', 'python_dotenv', 'flask_cors', 'winshell',
        'engineio.async_drivers.threading'
    ]
    
    for hidden_import in hidden_imports:
        cmd_parts.append(f'--hidden-import={hidden_import}')
    
    cmd_parts.extend(['--noconfirm', '--log-level=INFO'])
    cmd_parts.append('C3.py')
    
    cmd = ' '.join(cmd_parts)
    
    with open('build_command.txt', 'w') as f:
        f.write(cmd)
    
    print("Starting build process...")
    result = subprocess.run(cmd, shell=True).returncode
    
    if result == 0:
        print("\n✓ Executable built successfully!")
        dist_path = current_dir / 'dist' / 'PackageSystemInstaller.exe'
        if dist_path.exists():
            size_mb = dist_path.stat().st_size / (1024 * 1024)
            print(f"✓ Executable: {dist_path}")
            print(f"✓ Size: {size_mb:.1f} MB")
            return True  
        else:
            print("✗ Executable file not found after build")
            return False  
    else:
        print("\n✗ Executable build failed!")
        return False 

def kill_existing_processes(current_dir):
    """Kill any existing PackageSystemInstaller processes and remove old executable"""
    print("Cleaning up existing processes and files...")
    
    # Kill any running PackageSystemInstaller processes
    try:
        for proc in psutil.process_iter(['name', 'pid']):
            try:
                proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                if 'packagesysteminstaller' in proc_name:
                    print(f"Killing existing process: {proc.info['name']} (PID: {proc.info['pid']})")
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        print(f"Note: Could not kill processes: {e}")
    
    # Wait a moment for processes to terminate
    time.sleep(2)
    
    # Remove existing executable if it exists
    dist_path = current_dir / 'dist' / 'PackageSystemInstaller.exe'
    if dist_path.exists():
        try:
            os.remove(dist_path)
            print("✓ Removed existing executable")
            time.sleep(1)  # Wait for file system
        except PermissionError as e:
            print(f"✗ Could not remove existing executable: {e}")
            print("Please close any running PackageSystemInstaller.exe and try again")
            return False
        except Exception as e:
            print(f"Note: Could not remove executable: {e}")
    
    # Also clean up build directory
    build_dir = current_dir / 'build'
    if build_dir.exists():
        try:
            shutil.rmtree(build_dir)
            print("✓ Cleaned build directory")
        except Exception as e:
            print(f"Note: Could not clean build directory: {e}")
    
    return True

def create_installer_structure():
    """Create installer structure"""
    current_dir = Path(__file__).parent
    installer_dir = current_dir / "PackageSystemInstaller_Full"
    
    if installer_dir.exists():
        shutil.rmtree(installer_dir)
    installer_dir.mkdir(exist_ok=True)
    
    print("Creating installer structure...")
    
    items_to_copy = [
        'pythonInstallations', 'Templates', 'nssm-2.24 (1)',
        'C3.py', 'log_error.txt', 'log_output.txt', 'OAINITIALS.csv',
        'PackageLog_Python.py', 'WebApp.py', 
        'xampp-windows-x64-8.2.12-0-VS16-installer.exe',
        'clement_package_log.sql'
    ]
    
    for item_name in items_to_copy:
        item_path = current_dir / item_name
        if item_path.exists():
            try:
                if item_path.is_dir():
                    shutil.copytree(item_path, installer_dir / item_name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item_path, installer_dir / item_name)
                print(f"✓ {item_name}")
            except Exception as e:
                print(f"✗ {item_name}: {e}")
    
    # Create a proper startup batch file
    run_script = installer_dir / "StartPackageSystem.bat"
    with open(run_script, 'w') as f:
        f.write("""@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo    PACKAGE SYSTEM - STARTING SERVICES
echo ========================================
echo.

REM Kill existing processes first
echo Cleaning up existing processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im httpd.exe >nul 2>&1
taskkill /f /im mysqld.exe >nul 2>&1
timeout /t 3 /nobreak >nul

REM Start XAMPP services if available
echo Checking for XAMPP services...
if exist "C:\\xampp\\apache\\bin\\httpd.exe" (
    echo Starting Apache...
    start /B "" "C:\\xampp\\apache\\bin\\httpd.exe"
)

if exist "C:\\xampp\\mysql\\bin\\mysqld.exe" (
    echo Starting MySQL...
    start /B "" "C:\\xampp\\mysql\\bin\\mysqld.exe" "--defaults-file=C:\\xampp\\mysql\\bin\\my.ini"
)

REM Wait for services to initialize
echo Waiting for services to start...
timeout /t 10 /nobreak >nul

REM Start Flask WebApp
echo Starting Flask Web Application...
start "Flask WebApp" /min python WebApp.py

REM Wait a bit for Flask to start
timeout /t 5 /nobreak >nul

REM Start Package Scanner
echo Starting Package Scanner...
start "Package Scanner" /min python PackageLog_Python.py

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

REM Keep the window open
pause
""")
    
    print(f"✓ Installer created at: {installer_dir}")

if __name__ == "__main__":
    print("=" * 60)
    print("    PACKAGE SYSTEM - COMPLETE INSTALLATION")
    print("=" * 60)
    
    print("\nStarting build process...")
    success = build_executable_complete() 
    
    if success:  
        print("\nCreating installer...")
        create_installer_structure()
        
        print("\n" + "=" * 60)
        print("LAUNCHING ALL SERVICES...")
        print("=" * 60)
        
        launch_application()
        
        print("\n" + "=" * 60)
        print("INSTALLATION COMPLETE!")
        print("=" * 60)
        print("\nIf services didn't start properly:")
        print("1. Run 'StartPackageSystem.bat' manually from the installer folder")
        print("2. Or run 'Start C3.bat' if it exists on your desktop")
        print("3. Make sure XAMPP is installed and running")
    else:
        print("\n" + "=" * 60)
        print("BUILD FAILED!")
        print("=" * 60)
        print("\nPlease make sure:")
        print("1. Close any running PackageSystemInstaller.exe")
        print("2. Run this script again")
        print("3. If problem persists, restart your computer and try again")
    
    print("\nPress Enter to close this window...")
    input()