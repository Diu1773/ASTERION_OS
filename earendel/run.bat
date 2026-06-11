@chcp 65001 >nul
@echo off
setlocal EnableExtensions

REM Earendel launcher — venv 준비 후 서버 기동, 브라우저 자동 오픈.

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "VENV=%ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo [SETUP] Python venv 생성 중...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] venv 생성 실패 — Python 3.11+ 설치 확인
        pause
        exit /b 1
    )
)

"%PY%" -m pip install -q -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] 의존성 설치 실패
    pause
    exit /b 1
)

start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8520"
pushd "%ROOT%"
"%PY%" -m earendel
popd
