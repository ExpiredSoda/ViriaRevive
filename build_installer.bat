@echo off
setlocal
cd /d "%~dp0"

echo === ViriaRevive Installer Build ===
echo.

echo [*] Rebuilding ZIP app first so installer input is fresh...
call build.bat
if errorlevel 1 exit /b 1

for /f "delims=" %%V in ('venv\Scripts\python.exe -c "from version import APP_VERSION; print(APP_VERSION)"') do set "APP_VERSION=%%V"
for /f "delims=" %%V in ('venv\Scripts\python.exe -c "from version import APP_VERSION_QUAD; print(APP_VERSION_QUAD)"') do set "APP_VERSION_QUAD=%%V"
if "%APP_VERSION%"=="" (
    echo [!] Could not read APP_VERSION from version.py.
    exit /b 1
)

for /f "delims=" %%V in ('powershell -NoProfile -Command "(Get-Item 'dist\ViriaRevive\ViriaRevive.exe').VersionInfo.ProductVersion"') do set "BUILT_VERSION=%%V"
if not "%BUILT_VERSION%"=="%APP_VERSION%" (
    echo [!] Built EXE version %BUILT_VERSION% does not match APP_VERSION %APP_VERSION%.
    exit /b 1
)

venv\Scripts\python.exe scripts\check_version_sync.py
if errorlevel 1 exit /b 1

venv\Scripts\python.exe scripts\check_dependency_pins.py
if errorlevel 1 exit /b 1

venv\Scripts\python.exe scripts\check_release_safety.py --require-exists dist\ViriaRevive
if errorlevel 1 exit /b 1

set "ISCC_EXE="
for %%P in (
    "%LOCALAPPDATA%\Programs\Inno\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do (
    if not defined ISCC_EXE if exist "%%~P" set "ISCC_EXE=%%~P"
)
if "%ISCC_EXE%"=="" (
    for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do if "%ISCC_EXE%"=="" set "ISCC_EXE=%%P"
)
if "%ISCC_EXE%"=="" (
    echo [!] Inno Setup was not found.
    echo     Install Inno Setup from https://jrsoftware.org/isinfo.php
    echo     Then run build_installer.bat again.
    exit /b 1
)

if exist "release\ViriaReviveSetup-v%APP_VERSION%.exe" del /Q "release\ViriaReviveSetup-v%APP_VERSION%.exe"
if exist "release\ViriaReviveSetup-v%APP_VERSION%.exe.sha256" del /Q "release\ViriaReviveSetup-v%APP_VERSION%.exe.sha256"

echo [*] Using Inno Setup compiler: %ISCC_EXE%
"%ISCC_EXE%" /Q /DMyAppVersion=%APP_VERSION% /DMyAppVersionQuad=%APP_VERSION_QUAD% installer\ViriaRevive.iss
if errorlevel 1 exit /b 1

venv\Scripts\python.exe scripts\write_release_hashes.py "%APP_VERSION%" --include-installer
if errorlevel 1 exit /b 1

echo.
echo [+] Installer created: release\ViriaReviveSetup-v%APP_VERSION%.exe
endlocal
