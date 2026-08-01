@echo off
rem  ---------------------------------------------------------------
rem   Korean text must NOT be used in this file.
rem   Windows CMD reads .bat with the system code page, not UTF-8,
rem   so Korean here corrupts the commands themselves.
rem   All user-facing Korean lives in budget\_launcher.py instead.
rem  ---------------------------------------------------------------
title Budget
cd /d "%~dp0.."

where py >nul 2>&1
if %errorlevel%==0 (
  py "budget\_launcher.py"
  goto done
)

where python >nul 2>&1
if %errorlevel%==0 (
  python "budget\_launcher.py"
  goto done
)

echo.
echo   [ERROR] Python is not installed.
echo.
echo   1^) Open  https://www.python.org/downloads/
echo   2^) Click the yellow [Download Python] button
echo   3^) IMPORTANT: check [Add python.exe to PATH] while installing
echo   4^) Close this window, then run this file again
echo.

:done
echo.
pause
