@echo off
chcp 65001 > nul
title 부부 공동 가계부
cd /d "%~dp0.."

echo.
echo   부부 공동 가계부를 시작합니다.
echo.

REM ── 파이썬이 있는지 확인 ──────────────────────────────
where python > nul 2>&1
if errorlevel 1 (
  echo   [!] 파이썬이 설치되어 있지 않습니다.
  echo.
  echo   1) https://www.python.org/downloads/  에 접속
  echo   2) 노란 [Download Python] 버튼 클릭
  echo   3) 설치할 때 맨 아래 [Add python.exe to PATH] 를 반드시 체크
  echo   4) 설치가 끝나면 이 창을 닫고 시작.bat 를 다시 실행
  echo.
  pause
  exit /b 1
)

REM ── 처음이면 필요한 것들을 설치 ──────────────────────
python -c "import flask, openpyxl, waitress" > nul 2>&1
if errorlevel 1 (
  echo   처음 실행이라 필요한 것들을 받는 중입니다. 1~2분 걸립니다...
  echo.
  python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   [!] 설치에 실패했습니다. 인터넷 연결을 확인해 주세요.
    pause
    exit /b 1
  )
)

echo   브라우저가 곧 열립니다.
echo   가계부를 끄려면 이 검은 창을 닫으세요.
echo.

python budget\run.py
pause
