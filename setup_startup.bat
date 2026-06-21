@echo off
echo ============================================
echo   ViriaRevive — Windows Startup Setup
echo ============================================
echo.
echo Choose an option:
echo   [1] Enable auto-start on Windows login (minimized to tray)
echo   [2] Disable auto-start
echo   [3] Cancel
echo.
set /p choice="Enter choice (1/2/3): "

if "%choice%"=="1" goto enable
if "%choice%"=="2" goto disable
if "%choice%"=="3" goto done
echo Invalid choice.
goto done

:enable
echo.
echo Creating startup shortcut...

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_FOLDER%\ViriaRevive.lnk"
set "VBS_PATH=%~dp0ViriaRevive_Startup.vbs"
set "VIRIA_SHORTCUT=%SHORTCUT%"
set "VIRIA_VBS_PATH=%VBS_PATH%"
set "VIRIA_WORKDIR=%~dp0"

REM Create a shortcut using PowerShell (works on all Windows 10/11)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q = [char]34; $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($env:VIRIA_SHORTCUT); $s.TargetPath = 'wscript.exe'; $s.Arguments = $q + $env:VIRIA_VBS_PATH + $q; $s.WorkingDirectory = $env:VIRIA_WORKDIR; $s.Description = 'ViriaRevive - Auto-start minimized'; $s.Save()"

if exist "%SHORTCUT%" (
    echo.
    echo [OK] ViriaRevive will now auto-start when you log in.
    echo      It launches minimized to the system tray.
    echo      Shortcut: %SHORTCUT%
) else (
    echo [ERROR] Failed to create startup shortcut.
)
goto done

:disable
echo.
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ViriaRevive.lnk"
if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo [OK] Auto-start disabled. Shortcut removed.
) else (
    echo [OK] Auto-start was not enabled. Nothing to remove.
)
goto done

:done
echo.
pause
