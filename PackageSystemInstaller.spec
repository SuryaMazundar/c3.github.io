# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C3.py'],
    pathex=[],
    binaries=[],
    datas=[('pythonInstallations', 'pythonInstallations'), ('Templates', 'Templates'), ('nssm-2.24 (1)', 'nssm-2.24 (1)'), ('C3.py', '.'), ('log_error.txt', '.'), ('log_output.txt', '.'), ('PackageLog_Python.py', '.'), ('WebApp.py', '.'), ('xampp-windows-x64-8.2.12-0-VS16-installer.exe', '.'), ('clement_package_log.sql', '.')],
    hiddenimports=['psutil', 'psutil._psutil_windows', 'psutil._psutil_common', 'flask', 'flask_socketio', 'mysql.connector', 'pandas', 'openpyxl', 'keyboard', 'win32gui', 'win32con', 'win32api', 'win32process', 'requests', 'python_dotenv', 'flask_cors', 'winshell', 'engineio.async_drivers.threading'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PackageSystemInstaller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
