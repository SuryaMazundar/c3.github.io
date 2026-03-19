import os
import sys
import subprocess
import shutil
import ctypes
import tempfile
import urllib.request
import time
import datetime
import psutil
import signal
import mysql.connector
from pathlib import Path

class PackageInstaller:
    def __init__(self):
        self.install_dir = r"C:\C3 - Checkin Checkout Center"
        self.desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        self.python_installer_url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        
        # XAMPP installer
        self.xampp_installer_name = "xampp-windows-x64-8.2.12-0-VS16-installer.exe"
        self.xampp_installer = self.find_xampp_installer()
        
        # Requirements files
        self.requirements_file = "requirements.yml"
        
        self.backup_dir = os.path.join(self.install_dir, "backups")
        self.current_step = 0
        self.total_steps = 6
        self.found_port = None
    
    def show_progress(self, step_name, progress, total=100, custom_message=""):
        """Show progress bar for any step"""
        bar_length = 40
        filled_length = int(bar_length * progress // total)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        percent = int(100 * progress / total)
        
        if custom_message:
            sys.stdout.write(f'\r[{bar}] {percent}% - {step_name} - {custom_message}')
        else:
            sys.stdout.write(f'\r[{bar}] {percent}% - {step_name}')
        sys.stdout.flush()
        
        if progress >= total:
            print()

    def check_xampp_exists(self):
        """Check if XAMPP exists anywhere on C: drive - COMPREHENSIVE SEARCH"""
        print("Performing comprehensive XAMPP search on C: drive...")
        
        # Common XAMPP installation paths
        common_paths = [
            r"C:\xampp",
            r"C:\Program Files\xampp", 
            r"C:\Program Files (x86)\xampp"
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                print(f"XAMPP found at: {path}")
                return path
        
        # Comprehensive search entire C: drive for xampp
        print("Searching entire C: drive for XAMPP...")
        found_paths = []
        
        try:
            for root, dirs, files in os.walk("C:\\"):
                try:
                    # Look for xampp in directory names
                    if 'xampp' in root.lower():
                        # Check if it has typical XAMPP structure
                        if any(name in dirs for name in ['apache', 'mysql', 'htdocs']):
                            found_paths.append(root)
                            print(f"XAMPP found at: {root}")
                            return root
                    
                    # Also check for xampp-control.exe in files
                    for file in files:
                        if 'xampp-control' in file.lower() and file.endswith('.exe'):
                            xampp_path = os.path.dirname(root)
                            print(f"XAMPP found via control panel at: {xampp_path}")
                            return xampp_path
                            
                except (PermissionError, OSError):
                    continue
                    
        except Exception as e:
            print(f"Could not complete full C: drive search: {e}")
        
        if not found_paths:
            print(" XAMPP not found on C: drive")
            return None
        
        return found_paths[0]

    def check_python_exists(self):
        """Check if Python exists anywhere on C: drive - COMPREHENSIVE SEARCH"""
        print("Performing comprehensive Python search on C: drive...")
        
        # Quick check using system PATH
        python_commands = ['python', 'python3', 'py']
        
        for cmd in python_commands:
            try:
                result = subprocess.run([cmd, '--version'], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    print(f" Python found in PATH: {result.stdout.strip()}")
                    # Get actual Python executable path
                    which_result = subprocess.run(['where', cmd], capture_output=True, text=True, timeout=5)
                    if which_result.returncode == 0:
                        python_path = which_result.stdout.split('\n')[0].strip()
                        print(f"Python executable at: {python_path}")
                    return True
            except:
                continue
        
        # Comprehensive search for Python installations
        print("Searching entire C: drive for Python installations...")
        python_patterns = [
            r"C:\Python*",
            r"C:\Program Files\Python*",
            r"C:\Program Files (x86)\Python*", 
            r"C:\Users\*\AppData\Local\Programs\Python\Python*"
        ]
        
        found_installations = []
        
        try:
            # Search common patterns first
            import glob
            for pattern in python_patterns:
                paths = glob.glob(pattern)
                for path in paths:
                    python_exe = os.path.join(path, "python.exe")
                    if os.path.exists(python_exe):
                        found_installations.append(path)
                        print(f"Python found at: {python_exe}")
            
            # Also search for python.exe in Program Files
            for root, dirs, files in os.walk(r"C:\Program Files"):
                if 'python.exe' in files:
                    python_path = os.path.join(root, 'python.exe')
                    if os.path.exists(python_path):
                        found_installations.append(os.path.dirname(python_path))
                        print(f"Python found at: {python_path}")
                        
        except Exception as e:
            print(f"Search interrupted: {e}")
        
        if found_installations:
            return True
        
        print("Python not found on C: drive")
        return False

    def check_mysql_exists(self):
        """Check if MySQL exists anywhere on C: drive - COMPREHENSIVE SEARCH"""
        print(" Performing comprehensive MySQL search on C: drive...")
        
        # Check if MySQL service exists
        try:
            result = subprocess.run(['sc', 'query', 'mysql'], capture_output=True, text=True, timeout=10)
            if 'RUNNING' in result.stdout or 'STOPPED' in result.stdout:
                print("MySQL service found in Windows Services")
                return True
        except:
            pass
        
        # Check common MySQL paths
        common_paths = [
            r"C:\xampp\mysql\bin\mysql.exe",
            r"C:\Program Files\MySQL\*\bin\mysql.exe",
            r"C:\Program Files (x86)\MySQL\*\bin\mysql.exe"
        ]
        
        import glob
        for pattern in common_paths:
            paths = glob.glob(pattern)
            for path in paths:
                if os.path.exists(path):
                    print(f"MySQL found at: {path}")
                    return True
        
        # Search for mysqld.exe or mysql.exe anywhere
        print("Searching for MySQL executables...")
        try:
            for root, dirs, files in os.walk("C:\\"):
                try:
                    if any(file in ['mysqld.exe', 'mysql.exe'] for file in files):
                        mysql_path = os.path.join(root, 'mysql.exe')
                        if os.path.exists(mysql_path):
                            print(f"MySQL found at: {mysql_path}")
                            return True
                except (PermissionError, OSError):
                    continue
        except Exception as e:
            print(f"MySQL search interrupted: {e}")
        
        print("MySQL not found on C: drive")
        return False

    def kill_existing_services(self):
        """Clean up existing processes with user feedback"""
        try:
            print("Cleaning up existing processes...")
            
            target_processes = [
                "mysqld.exe", "mysql.exe", "httpd.exe", "apache.exe",
                "python.exe", "WebApp", "PackageLog_Python"
            ]
            
            killed_count = 0
            current_pid = os.getpid()
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['pid'] == current_pid:
                        continue
                        
                    proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                    cmdline = proc.info.get('cmdline', [])
                    
                    should_kill = False
                    if any(target in proc_name for target in target_processes):
                        should_kill = True
                    if cmdline and any('WebApp' in str(arg) for arg in cmdline):
                        should_kill = True
                    if cmdline and any('PackageLog_Python' in str(arg) for arg in cmdline):
                        should_kill = True
                    
                    if should_kill:
                        try:
                            proc.terminate()
                            proc.wait(timeout=3)
                            killed_count += 1
                            print(f"Closed: {proc.info['name']}")
                        except:
                            try:
                                proc.kill()
                                killed_count += 1
                                print(f"Force closed: {proc.info['name']}")
                            except:
                                print(f" Could not close: {proc.info['name']}")
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            
            if killed_count > 0:
                print(f"Cleaned up {killed_count} processes")
            else:
                print("No processes to clean up")
                
            time.sleep(2)
                
        except Exception as e:
            print(f"Warning during cleanup: {e}")

    def find_available_port(self, start_port=5300, max_attempts=50):
        """Find an available port starting from start_port"""
        import socket
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue
        return start_port

    def find_xampp_installer(self):
        """Find XAMPP installer in current directory or install directory"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Check current directory first
        current_path = os.path.join(current_dir, self.xampp_installer_name)
        if os.path.exists(current_path):
            print(f"Found XAMPP installer in current directory")
            return current_path
        
        # Check current directory with different case sensitivity
        for file in os.listdir(current_dir):
            if file.lower() == self.xampp_installer_name.lower():
                found_path = os.path.join(current_dir, file)
                print(f"Found XAMPP installer: {file}")
                return found_path
        
        # Check install directory
        install_path = os.path.join(self.install_dir, self.xampp_installer_name)
        if os.path.exists(install_path):
            print(f"Found XAMPP installer in install directory")
            return install_path
        
        # If we're running from PyInstaller bundle
        if hasattr(sys, '_MEIPASS'):
            meipass_path = os.path.join(sys._MEIPASS, self.xampp_installer_name)
            if os.path.exists(meipass_path):
                print(f"Found XAMPP installer in bundle")
                return meipass_path
        
        print(f"XAMPP installer not found: {self.xampp_installer_name}")
        return None

    def find_requirements_file(self):
        """Find requirements file (YAML or TXT)"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Check for YAML file first
        current_path = os.path.join(current_dir, self.requirements_file)
        if os.path.exists(current_path):
            print(f"Found {self.requirements_file} in current directory")
            return current_path
        
        # Check for TXT file as fallback
        current_txt_path = os.path.join(current_dir, self.requirements_txt)
        if os.path.exists(current_txt_path):
            print(f"Found {self.requirements_txt} in current directory")
            return current_txt_path
        
        # Check install directory
        install_path = os.path.join(self.install_dir, self.requirements_file)
        if os.path.exists(install_path):
            print(f"Found {self.requirements_file} in install directory")
            return install_path
        
        install_txt_path = os.path.join(self.install_dir, self.requirements_txt)
        if os.path.exists(install_txt_path):
            print(f"Found {self.requirements_txt} in install directory")
            return install_txt_path
        
        # If we're running from PyInstaller bundle
        if hasattr(sys, '_MEIPASS'):
            meipass_path = os.path.join(sys._MEIPASS, self.requirements_file)
            if os.path.exists(meipass_path):
                print(f"Found {self.requirements_file} in bundle")
                return meipass_path
            
            meipass_txt_path = os.path.join(sys._MEIPASS, self.requirements_txt)
            if os.path.exists(meipass_txt_path):
                print(f"Found {self.requirements_txt} in bundle")
                return meipass_txt_path
        
        print(f"Requirements file not found, will install packages individually")
        return None

    def is_admin(self):
        """Check if the script is running with administrator privileges"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False
    
    def run_as_admin(self):
        """Relaunch with administrator privileges - PyInstaller compatible"""
        if not self.is_admin():
            print("Requesting administrator privileges...")
            
            if getattr(sys, 'frozen', False):
                # Running as a PyInstaller .exe - relaunch the exe itself
                exe_path = sys.executable
                ctypes.windll.shell32.ShellExecuteW(None, "runas", exe_path, None, None, 1)
            else:
                # Running as a normal .py script
                script = os.path.abspath(__file__)
                ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}"', None, 1)
            
            sys.exit()

    def install_xampp(self):
        """Install XAMPP only if not found on C: drive and start services automatically"""
        try:
            print("\n" + "="*50)
            print("STEP 1: CHECKING XAMPP INSTALLATION")
            print("="*50)
            
            # Check if XAMPP already exists
            existing_xampp = self.check_xampp_exists()
            if existing_xampp:
                print("Using existing XAMPP installation")
                # Start services automatically for existing installation
                self.start_xampp_services_automatically(existing_xampp)
                return existing_xampp
            
            print("XAMPP not found, preparing installation...")
            
            if not self.xampp_installer or not os.path.exists(self.xampp_installer):
                print(f"XAMPP installer not found")
                print("Please download XAMPP manually and place it in the same directory as this installer")
                return None
            
            # Copy installer to install directory
            installer_dest = os.path.join(self.install_dir, self.xampp_installer_name)
            if self.xampp_installer != installer_dest:
                shutil.copy2(self.xampp_installer, installer_dest)
            
            # XAMPP silent installation - USING RELIABLE APPROACH
            print("Installing XAMPP (this may take 5-10 minutes)...")
            self.show_progress("Installing XAMPP", 0, 100, "Starting...")
            
            # Use the reliable installation approach
            install_cmd = f'"{self.xampp_installer}" --mode unattended --unattendedmodeui minimal'
            
            start_time = time.time()
            process = subprocess.Popen(install_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Show progress while installing
            while process.poll() is None:
                elapsed = time.time() - start_time
                progress = min(90, int((elapsed / 600) * 90))  # Max 10 minutes
                self.show_progress("Installing XAMPP", progress, 100, f"Installing... {int(elapsed)}s")
                time.sleep(2)
            
            # Wait for completion with longer timeout
            try:
                process.wait(timeout=600)  # 10 minute timeout
            except subprocess.TimeoutExpired:
                print("\nXAMPP installation timed out")
                return None
                
            self.show_progress("Installing XAMPP", 100, 100, "Complete!")
            
            if process.returncode == 0:
                print("\nXAMPP installed successfully!")
                time.sleep(2)
                
                # Start services automatically after installation
                xampp_path = r"C:\xampp"
                self.start_xampp_services_automatically(xampp_path)
                
                return xampp_path
            else:
                print(f"\nXAMPP installation failed with code: {process.returncode}")
                return None
                
        except Exception as e:
            print(f"\nError with XAMPP: {e}")
            return None

    def start_xampp_services_automatically(self, xampp_path):
        """Start XAMPP services immediately and reliably in background - FUNCTIONAL APPROACH"""
        try:
            print("Starting XAMPP services automatically in background...")
            
            # Kill any existing XAMPP processes first
            self.kill_existing_services()
            time.sleep(3)
            
            # Start Apache using reliable approach
            print("Starting Apache...")
            apache_success = self.start_apache_directly(xampp_path)
            
            time.sleep(5)  # Give Apache time to start
            
            # Start MySQL using reliable approach
            print("Starting MySQL...")
            mysql_success = self.start_mysql_directly(xampp_path)
            
            # Wait longer for services to initialize
            print("Waiting for services to initialize (15 seconds)...")
            time.sleep(15)
            
            # Check service status
            mysql_running = self.check_mysql_running(xampp_path)
            apache_running = self.check_apache_running()
            
            print(f"Apache status: {'RUNNING' if apache_running else 'NOT RUNNING'}")
            print(f"MySQL status: {'RUNNING' if mysql_running else 'NOT RUNNING'}")
            
            # If services didn't start, try alternative method
            if not (apache_running and mysql_running):
                print("Some services didn't start, trying alternative startup...")
                self.start_services_alternative(xampp_path)
                time.sleep(10)
                
                # Re-check status
                mysql_running = self.check_mysql_running(xampp_path)
                apache_running = self.check_apache_running()
                print(f"After alternative start - Apache: {'RUNNING' if apache_running else 'NOT RUNNING'}, MySQL: {'RUNNING' if mysql_running else 'NOT RUNNING'}")
            
            return mysql_running or apache_running
                
        except Exception as e:
            print(f"Error starting services: {e}")
            return False

    def start_services_alternative(self, xampp_path):
        """Alternative method to start services using batch file approach"""
        try:
            # Create a temporary batch file to start services
            batch_content = f"""@echo off
cd /d "{xampp_path}"
echo Starting Apache...
start /B apache\\bin\\httpd.exe
timeout /t 5 /nobreak >nul
echo Starting MySQL...
start /B mysql\\bin\\mysqld.exe --defaults-file=mysql\\bin\\my.ini
echo Services started in background
"""
            batch_file = os.path.join(self.install_dir, "start_services_temp.bat")
            with open(batch_file, 'w') as f:
                f.write(batch_content)
            
            # Run the batch file
            subprocess.Popen(f'cmd /c "{batch_file}"', shell=True)
            
            # Clean up batch file after a delay
            time.sleep(2)
            try:
                os.remove(batch_file)
            except:
                pass
                
        except Exception as e:
            print(f"Alternative startup failed: {e}")

    def start_apache_directly(self, xampp_path):
        """Start Apache directly using httpd.exe - RELIABLE METHOD"""
        try:
            httpd_exe = os.path.join(xampp_path, "apache", "bin", "httpd.exe")
            if not os.path.exists(httpd_exe):
                print(f"Apache httpd.exe not found at: {httpd_exe}")
                return False
            
            print(f"Starting Apache from: {httpd_exe}")
            
            # Start Apache as a background process
            CREATE_NO_WINDOW = 0x08000000
            process = subprocess.Popen(
                [httpd_exe],
                cwd=os.path.join(xampp_path, "apache", "bin"),
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Give it a moment to start
            time.sleep(3)
            return process.poll() is None  # Return True if still running
            
        except Exception as e:
            print(f"Error starting Apache: {e}")
            return False

    def start_mysql_directly(self, xampp_path):
        """Start MySQL directly using mysqld.exe - RELIABLE METHOD"""
        try:
            mysqld_exe = os.path.join(xampp_path, "mysql", "bin", "mysqld.exe")
            if not os.path.exists(mysqld_exe):
                print(f"MySQL mysqld.exe not found at: {mysqld_exe}")
                return False
            
            print(f"Starting MySQL from: {mysqld_exe}")
            
            # Start MySQL as a background process
            CREATE_NO_WINDOW = 0x08000000
            my_ini_path = os.path.join(xampp_path, "mysql", "bin", "my.ini")
            
            if os.path.exists(my_ini_path):
                process = subprocess.Popen(
                    [mysqld_exe, f"--defaults-file={my_ini_path}"],
                    cwd=os.path.join(xampp_path, "mysql", "bin"),
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            else:
                # Fallback without config file
                process = subprocess.Popen(
                    [mysqld_exe],
                    cwd=os.path.join(xampp_path, "mysql", "bin"),
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            # Give it a moment to start
            time.sleep(5)
            return process.poll() is None  # Return True if still running
            
        except Exception as e:
            print(f"Error starting MySQL: {e}")
            return False

    def check_mysql_running(self, xampp_path):
        """Check if MySQL is running"""
        try:
            mysql_exe = os.path.join(xampp_path, "mysql", "bin", "mysql.exe")
            if os.path.exists(mysql_exe):
                # Try to connect to MySQL
                result = subprocess.run(
                    [mysql_exe, "-u", "root", "-e", "SELECT 1;"], 
                    capture_output=True, 
                    text=True, 
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                return result.returncode == 0
        except subprocess.TimeoutExpired:
            print("MySQL check timed out")
        except Exception as e:
            print(f"MySQL check error: {e}")
        return False

    def check_apache_running(self):
        """Check if Apache is running"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('127.0.0.1', 80))
            sock.close()
            return result == 0
        except:
            return False

    def create_mysql_database(self, xampp_path):
        """Create MySQL database only if MySQL exists"""
        try:
            print("\n" + "="*50)
            print("STEP 2: SETTING UP DATABASE")
            print("="*50)
            
            # Check if MySQL exists first
            if not self.check_mysql_exists():
                print("MySQL not available - cannot create database")
                return False
            
            mysql_exe = os.path.join(xampp_path, "mysql", "bin", "mysql.exe")
            
            if not os.path.exists(mysql_exe):
                print("MySQL executable not found")
                return False
            
            # Fix file extension
            sql_file_path = os.path.join(self.install_dir, "clement_package_log.sql")
            if not os.path.exists(sql_file_path):
                print(f"SQL file not found: {sql_file_path}")
                return False
            
            print("Checking database...")
            
            # Check if database already exists
            check_db_args = [mysql_exe, "-u", "root", "-e", "SHOW DATABASES LIKE 'clement_package_log';"]
            
            result = subprocess.run(check_db_args, capture_output=True, text=True, timeout=30)
            database_exists = "clement_package_log" in result.stdout
            
            if database_exists:
                print("Database 'clement_package_log' already exists")
                return True
            else:
                # Create and import database
                print("Creating new database...")
                
                create_db_args = [mysql_exe, "-u", "root", "-e", "CREATE DATABASE clement_package_log;"]
                result = subprocess.run(create_db_args, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    print("Database created successfully")
                    
                    # Import the SQL file
                    print("Importing data...")
                    import_args = [mysql_exe, "-u", "root", "clement_package_log", "-e", f"SOURCE '{sql_file_path}'"]
                    result = subprocess.run(import_args, capture_output=True, text=True, timeout=120)
                    
                    if result.returncode == 0:
                        print("Data imported successfully!")
                        return True
                    else:
                        print("Error importing data")
                        return False
                else:
                    print("Error creating database")
                    return False
                
        except Exception as e:
            print(f"Error creating database: {e}")
            return False
    
    def install_python(self):
        """Install Python only if not found on C: drive"""
        try:
            print("\n" + "="*50)
            print("STEP 3: CHECKING PYTHON INSTALLATION")
            print("="*50)
            
            # Check if Python already exists
            if self.check_python_exists():
                print("Using existing Python installation")
                return True
            
            print("Python not found, downloading installer...")
            
            # Download Python installer
            temp_dir = tempfile.gettempdir()
            python_installer_path = os.path.join(temp_dir, "python_installer.exe")
            
            def download_progress(count, block_size, total_size):
                percent = min(100, int(count * block_size * 100 / total_size))
                self.show_progress("Downloading Python", percent, 100, f"{percent}%")
            
            print("Downloading Python installer...")
            try:
                urllib.request.urlretrieve(self.python_installer_url, python_installer_path, download_progress)
                print("\nDownload complete!")
            except Exception as e:
                print(f"\nFailed to download Python: {e}")
                return False
            
            # Install Python
            install_args = [
                python_installer_path,
                "/quiet",
                "InstallAllUsers=1",
                "PrependPath=1",
                "Include_test=0",
                "AssociateFiles=0",
                "Shortcuts=0"
            ]
            
            print("Installing Python (this may take a few minutes)...")
            self.show_progress("Installing Python", 0, 100, "Starting...")
            
            start_time = time.time()
            process = subprocess.Popen(install_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            while process.poll() is None:
                elapsed = time.time() - start_time
                progress = min(90, int((elapsed / 180) * 90))  # Max 3 minutes
                self.show_progress("Installing Python", progress, 100, f"Installing... {int(elapsed)}s")
                time.sleep(2)
            
            process.wait(timeout=10)
            self.show_progress("Installing Python", 100, 100, "Complete!")
            
            # Clean up installer
            try:
                if os.path.exists(python_installer_path):
                    os.remove(python_installer_path)
            except:
                pass
            
            print("Python installed successfully!")
            
            # Verify installation
            if self.check_python_exists():
                return True
            else:
                print("Python installation verification failed")
                return False
            
        except Exception as e:
            print(f"Error installing Python: {e}")
            return False
    
    def install_from_yaml(self, yaml_path):
        """Install packages from YAML environment file"""
        try:
            print("Installing packages from YAML environment file...")
            
            # First try conda environment creation
            try:
                print("Attempting conda environment creation...")
                result = subprocess.run([
                    "conda", "env", "create", "-f", yaml_path
                ], timeout=600, capture_output=True, text=True)
                
                if result.returncode == 0:
                    print("Conda environment created successfully!")
                    return True
                else:
                    print("Conda environment creation failed, trying pip installation...")
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                print(f"Conda not available or failed: {e}")
            
            # Fallback to pip installation from YAML
            try:
                import yaml
                with open(yaml_path, 'r') as f:
                    env_data = yaml.safe_load(f)
                
                pip_packages = env_data.get('dependencies', [])
                
                pip_list = []
                for item in pip_packages:
                    if isinstance(item, str) and item.startswith('pip'):
                        continue
                    elif isinstance(item, dict) and 'pip' in item:
                        pip_list = item['pip']
                        break
                
                if pip_list:
                    print(f"Installing {len(pip_list)} packages via pip...")
                    
                    for package in pip_list:
                        print(f"Installing {package}...")
                        result = subprocess.run([
                            sys.executable, "-m", "pip", "install", 
                            package, "--quiet", "--no-warn-script-location"
                        ], timeout=120, capture_output=True, text=True)
                        
                        if result.returncode == 0:
                            print(f"✓ {package}")
                        else:
                            print(f"✗ {package} (may need manual installation)")
                    
                    print("YAML package installation completed!")
                    return True
                
            except Exception as e:
                print(f"YAML pip installation failed: {e}")
            
            return False
            
        except Exception as e:
            print(f"Error installing from YAML: {e}")
            return False
    
    def install_python_packages(self):
        """Install required Python packages using YAML, requirements.yml, or individual packages"""
        try:
            print("\n" + "="*50)
            print("STEP 4: INSTALLING PYTHON PACKAGES")
            print("="*50)
            
            # Check if Python exists first
            if not self.check_python_exists():
                print("Python not available - cannot install packages")
                return False
            
            # Try to find and use requirements file first
            requirements_path = self.find_requirements_file()
            
            if requirements_path and os.path.exists(requirements_path):
                if requirements_path.endswith('.yml') or requirements_path.endswith('.yaml'):
                    # Use YAML installation
                    if self.install_from_yaml(requirements_path):
                        print("All packages installed successfully from YAML!")
                        return True
                    else:
                        print("YAML installation failed, trying requirements.yml fallback...")
                else:
                    # Use traditional requirements.yml
                    print("Installing packages from requirements.yml...")
                    
                    result = subprocess.run([
                        sys.executable, "-m", "pip", "install", 
                        "-r", requirements_path,
                        "--quiet", "--no-warn-script-location"
                    ], timeout=300, capture_output=True, text=True)
                    
                    if result.returncode == 0:
                        print("All packages installed successfully from requirements.yml!")
                        return True
                    else:
                        print("Some packages failed from requirements.yml, trying individual installation...")
            
            # Individual package installation (fallback) - WITH COMPATIBLE VERSIONS
            packages = [
                "flask==3.0.0",
                "keyboard==0.13.5", 
                "requests==2.31.0",
                "mysql-connector-python==8.1.0",
                "python-socketio==5.10.0",
                "flask-socketio==5.3.6",
                "python-dotenv==1.0.0",
                "flask-cors==4.0.0",
                "eventlet==0.33.3",  # COMPATIBLE VERSION FOR PYTHON 3.12
                "pywin32==306",
                "winshell==0.6",
                "pandas==2.1.4",
                "openpyxl==3.1.2",
                "psutil==5.9.6",
                "pyyaml==6.0.1"  # For YAML parsing
            ]
            
            total_packages = len(packages)
            successful_installs = 0
            
            print(f"Installing {total_packages} packages individually...")
            
            for i, package in enumerate(packages):
                progress = int((i / total_packages) * 100)
                self.show_progress("Installing Packages", progress, 100, f"{package}...")
                
                print(f"Installing {package}...")
                try:
                    result = subprocess.run([
                        sys.executable, "-m", "pip", "install", 
                        package, "--quiet", "--no-warn-script-location"
                    ], timeout=120, capture_output=True, text=True)
                    
                    if result.returncode == 0:
                        print(f"✓ {package}")
                        successful_installs += 1
                    else:
                        print(f"✗ {package} installation had issues")
                except subprocess.TimeoutExpired:
                    print(f"✗ {package} installation timed out")
                except Exception as e:
                    print(f"✗ {package} installation error: {e}")
            
            self.show_progress("Installing Packages", 100, 100, "Package installation completed!")
            
            success_rate = (successful_installs / total_packages) * 100
            print(f"Package installation completed: {successful_installs}/{total_packages} packages ({success_rate:.1f}%)")
            
            if successful_installs >= total_packages * 0.8:  # At least 80% success
                print("Package installation successful enough to continue")
                return True
            else:
                print("Some packages failed to install, but continuing...")
                return True
                
        except Exception as e:
            print(f"Error installing Python packages: {e}")
            return False
        
    def copy_application_files(self):
        """Copy all application files with progress"""
        try:
            print("\n" + "="*50)
            print("STEP 5: COPYING APPLICATION FILES")
            print("="*50)
            
            # Determine current directory
            if hasattr(sys, '_MEIPASS'):
                current_dir = sys._MEIPASS
                print("Running from installer bundle")
            else:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                print("Running from script directory")
            
            # Files to copy - FIXED FILE EXTENSIONS
            files_to_copy = [
                "WebApp.py",
                "PackageLog_Python.py", 
                "OAINITIALS.csv",
                "clement_package_log.sql",
                "requirements.yml"
            ]
            
            total_files = len(files_to_copy) + 1  # +1 for Templates directory
            
            # Copy individual files
            for i, file in enumerate(files_to_copy):
                progress = int((i / total_files) * 100)
                self.show_progress("Copying Files", progress, 100, f"{file}...")
                
                source = os.path.join(current_dir, file)
                dest = os.path.join(self.install_dir, file)
                
                if os.path.exists(source):
                    try:
                        shutil.copy2(source, dest)
                        print(f"{file}")
                    except Exception as e:
                        print(f"{file}: {e}")
                else:
                    print(f"{file} not found")
            
            # Copy Templates directory
            self.show_progress("Copying Files", 90, 100, "Templates...")
            templates_source = os.path.join(current_dir, "Templates")
            templates_dest = os.path.join(self.install_dir, "Templates")
            
            if os.path.exists(templates_source):
                try:
                    if os.path.exists(templates_dest):
                        shutil.rmtree(templates_dest)
                    shutil.copytree(templates_source, templates_dest)
                    print("Templates directory")
                except Exception as e:
                    print(f"Templates: {e}")
            else:
                print("Templates directory not found")
            
            self.show_progress("Copying Files", 100, 100, "All files copied!")
            print("File copy operation completed!")
            return True
            
        except Exception as e:
            print(f"Error copying files: {e}")
            return False

    def create_startup_batch(self):
        """Create the startup batch file with automatic service startup - HIDDEN WINDOWS"""
        try:
            print("\n" + "="*50)
            print("STEP 6: CREATING STARTUP FILES")
            print("="*50)
            
            # Find available port
            if not self.found_port:
                self.found_port = self.find_available_port()
            
            # Write the port to a file that WebApp.py can read
            port_file_path = os.path.join(self.install_dir, "web_port.txt")
            with open(port_file_path, "w") as f:
                f.write(str(self.found_port))
            print(f"Port {self.found_port} saved to web_port.txt")
            
            batch_file_path = os.path.join(self.install_dir, "Start C3.bat") 
        
            with open(batch_file_path, "w", encoding='utf-8') as f:
                f.write(f"""@echo off
    chcp 65001 >nul
    cd /d "{self.install_dir}"

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
    start /B "" "C:\\xampp\\apache\\bin\\httpd.exe"
    timeout /t 3 /nobreak >nul

    REM Start MySQL directly in background
    echo Starting MySQL...
    start /B "" "C:\\xampp\\mysql\\bin\\mysqld.exe" "--defaults-file=C:\\xampp\\mysql\\bin\\my.ini"
    timeout /t 5 /nobreak >nul

    REM Wait for services
    echo Waiting for services to start...
    timeout /t 5 /nobreak >nul

    REM Use the pre-determined port
    echo Using port {self.found_port} for web server...

    REM Start Flask web server in HIDDEN window
    echo Starting Flask web server (hidden)...
    start "Flask Web Server" /min python WebApp.py
    timeout /t 3 /nobreak >nul

    REM Start package scanner in HIDDEN window  
    echo Starting package scanner (hidden)...
    start "Package Scanner" /min python PackageLog_Python.py
    timeout /t 2 /nobreak >nul

    echo Opening web browser...
    start "" "http://localhost:{self.found_port}"

    echo.
    echo ========================================
    echo      SERVICES STARTED SUCCESSFULLY!
    echo.
    echo  Web Interface: http://localhost:{self.found_port}
    echo.
    echo  All services are running in background.
    echo  You can safely close this window.
    echo ========================================
    echo.
    pause
    """)
            
            # Create desktop shortcut
            desktop_bat_path = os.path.join(self.desktop_path, "Start C3.bat")
            try:
                shutil.copy2(batch_file_path, desktop_bat_path)
                # Also copy the port file to desktop if running from there
                desktop_port_file = os.path.join(self.desktop_path, "web_port.txt")
                shutil.copy2(port_file_path, desktop_port_file)
                print(f"Desktop shortcut created: Start C3.bat")
            except Exception as e:
                print(f"Could not create desktop shortcut: {e}")
                print(f"Batch file created at: {batch_file_path}")
            
            print("Startup files created successfully!")
            return True
            
        except Exception as e:
            print(f"Error creating startup files: {e}")
            return False

    def install(self):
        """Main installation method - INSTALLS MISSING COMPONENTS AUTOMATICALLY"""
        print("STARTING C3 - CHECKIN CHECKOUT CENTER INSTALLATION")
        print("=" * 60)
        print("Checking for existing installations on C: drive...")
        print("Will automatically install any missing components.")
        print("=" * 60)
        time.sleep(2)
        
        # Run as administrator
        self.run_as_admin()
        
        # Clean up existing processes first
        self.kill_existing_services()
        
        # Create installation directory
        os.makedirs(self.install_dir, exist_ok=True)
        print(f"Installation directory: {self.install_dir}")

        self.found_port = self.find_available_port()
        print(f"Using port: {self.found_port}")
        
        # Copy application files first (always copy these)
        if not self.copy_application_files():
            print("Failed to copy application files")
            return False
        
        # Install XAMPP (only if not found)
        xampp_path = self.install_xampp()
        if not xampp_path:
            print("XAMPP installation failed or not found")
            # Ask user if they want to continue
            response = input("Continue without XAMPP? (y/n): ").lower().strip()
            if response != 'y':
                return False
        
        # Create MySQL database (only if MySQL exists)
        if xampp_path and not self.create_mysql_database(xampp_path):
            print("Database setup had issues, but continuing...")
        
        # Install Python (only if not found)
        if not self.install_python():
            print("Python installation failed or not found")
            response = input("Continue without Python? (y/n): ").lower().strip()
            if response != 'y':
                return False
        
        # Install Python packages (only if Python exists)
        if not self.install_python_packages():
            print("Python package installation had issues, but continuing...")
        
        # Create startup batch file (always create this)
        if not self.create_startup_batch():
            print("Startup file creation had issues")
        
        # Final success message
        print("\n" + "=" * 60)
        print("INSTALLATION COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        print(f"Application installed in: {self.install_dir}")
        print("Desktop shortcut created: 'Start C3.bat'")
        print("\nTO START THE APPLICATION:")
        print("   1. Double-click 'Start C3.bat' on your desktop")
        print("   2. Services will start automatically in the background")
        print("   3. The application will open in your web browser")
        print(f"   4. Use your initials to login at: http://localhost:{self.found_port}")
        print("\nTIPS:")
        print("   • Apache and MySQL start automatically in the background")
        print("   • No manual service startup required")
        print("   • Close the command window after the browser opens")
        print("=" * 60)
        
        # Auto-close after success
        print("\nThis window will close automatically in 15 seconds...")
        time.sleep(15)
        return True

if __name__ == "__main__":
    installer = PackageInstaller()
    success = installer.install()
    
    # Auto-close on completion
    if success:
        print("Installation complete! Closing...")
        time.sleep(2)
    else:
        print("Installation failed. Please check the errors above.")
        input("Press Enter to exit...")