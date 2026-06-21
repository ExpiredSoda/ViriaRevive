@echo off
setlocal
cd /d "%~dp0"

echo === ViriaRevive Build ===
echo.

if not exist "venv\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    set "VENV_CREATED="
    py -3.12 -m venv venv >nul 2>nul
    if not errorlevel 1 set "VENV_CREATED=1"
    if not defined VENV_CREATED (
        py -3.11 -m venv venv >nul 2>nul
        if not errorlevel 1 set "VENV_CREATED=1"
    )
    if not defined VENV_CREATED (
        py -3 -m venv venv >nul 2>nul
        if not errorlevel 1 set "VENV_CREATED=1"
    )
    if not defined VENV_CREATED (
        python -m venv venv
        if not errorlevel 1 set "VENV_CREATED=1"
    )
    if not defined VENV_CREATED (
        echo [!] Could not create venv. Install Python 3.11+ and make sure the Python Launcher ^(py.exe^) or python.exe is available.
        exit /b 1
    )
)

venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [!] ViriaRevive builds require Python 3.11 or newer. Delete venv and rerun after installing Python 3.11+.
    exit /b 1
)

echo [*] Updating build environment...
venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
venv\Scripts\python.exe scripts\check_dependency_pins.py
if errorlevel 1 exit /b 1
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

for /f "delims=" %%V in ('venv\Scripts\python.exe -c "from version import APP_VERSION; print(APP_VERSION)"') do set "APP_VERSION=%%V"
if "%APP_VERSION%"=="" (
    echo [!] Could not read APP_VERSION from version.py.
    exit /b 1
)
echo [*] Building ViriaRevive v%APP_VERSION%...

if not exist release mkdir release
if exist "release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip" del /q "release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip"
if exist "release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip.sha256" del /q "release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip.sha256"
if exist "release\ViriaRevive-Windows-x64.zip" del /q "release\ViriaRevive-Windows-x64.zip"
if exist "release\ViriaRevive-Windows-x64.zip.sha256" del /q "release\ViriaRevive-Windows-x64.zip.sha256"

venv\Scripts\python.exe scripts\check_version_sync.py
if errorlevel 1 exit /b 1

echo [*] Generating tray icon...
venv\Scripts\python.exe -c "from tray import _create_icon_image; _create_icon_image(); print('[+] Tray icon generated')"

echo [*] Building with PyInstaller...
venv\Scripts\pyinstaller.exe viria.spec --noconfirm --clean
if errorlevel 1 exit /b 1

if not exist "dist\ViriaRevive\ViriaRevive.exe" (
    echo [!] Build failed. dist\ViriaRevive\ViriaRevive.exe was not created.
    exit /b 1
)

echo [*] Adding release support files...
copy /Y README.md dist\ViriaRevive\README.md >nul
copy /Y LICENSE dist\ViriaRevive\LICENSE >nul
copy /Y client_secrets.example.json dist\ViriaRevive\client_secrets.example.json >nul
copy /Y ViriaRevive.vbs dist\ViriaRevive\ViriaRevive.vbs >nul
copy /Y ViriaRevive_Startup.vbs dist\ViriaRevive\ViriaRevive_Startup.vbs >nul
copy /Y setup_startup.bat dist\ViriaRevive\setup_startup.bat >nul
if exist bin (
    if not exist dist\ViriaRevive\bin mkdir dist\ViriaRevive\bin
    for %%F in (README.md ffmpeg.exe ffprobe.exe) do (
        if exist "bin\%%F" copy /Y "bin\%%F" "dist\ViriaRevive\bin\%%F" >nul
    )
)

echo [*] Running release safety scan...
venv\Scripts\python.exe scripts\check_release_safety.py --require-exists dist\ViriaRevive
if errorlevel 1 exit /b 1

echo [*] Creating ZIP package...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$versioned='release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip'; $latest='release\ViriaRevive-Windows-x64.zip'; Compress-Archive -Path 'dist\ViriaRevive\*' -DestinationPath $versioned -Force; Copy-Item $versioned $latest -Force"
if errorlevel 1 exit /b 1
venv\Scripts\python.exe scripts\write_release_hashes.py "%APP_VERSION%"
if errorlevel 1 exit /b 1

echo.
echo [+] Build successful.
echo     App: dist\ViriaRevive\ViriaRevive.exe
echo     ZIP: release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip
echo     Latest ZIP copy: release\ViriaRevive-Windows-x64.zip
echo     SHA256: release\ViriaRevive-v%APP_VERSION%-Windows-x64.zip.sha256
echo.
echo Optional next step:
echo     build_installer.bat
echo.
echo Runtime data is not bundled. Installed builds store clips, tokens, and state
echo in %%LOCALAPPDATA%%\ViriaRevive.

endlocal
