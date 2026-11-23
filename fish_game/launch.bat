@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM ===== ESP32 IP: Thonny 창에 출력된 "ESP32 IP" 값으로 바꾸세요 =====
set ESP_IP=172.30.1.93
REM 실행할 때 IP를 직접 넣어도 됩니다: launch.bat 192.168.0.42
if not "%~1"=="" set ESP_IP=%~1

set PY=python
where python >nul 2>&1 || set PY=py
%PY% --version >nul 2>&1 || (echo [오류] Python이 없습니다. python.org 에서 3.10~3.13 설치 후 "Add to PATH" 체크 & pause & exit /b 1)

%PY% -c "import cv2,numpy" >nul 2>&1
if errorlevel 1 (
  echo [설치] OpenCV / numpy 설치 중...
  %PY% -m pip install "opencv-python>=4.8" "numpy>=1.24" || (echo [오류] 설치 실패 & pause & exit /b 1)
)
%PY% -c "import mediapipe" >nul 2>&1
if errorlevel 1 (
  echo [설치] MediaPipe 설치 시도 ^(실패해도 LED 인식은 동작^)...
  %PY% -m pip install "mediapipe>=0.10,<0.11"
)

echo ESP32 주소: %ESP_IP%
start "" http://localhost:8000
%PY% bridge.py --esp %ESP_IP% %2 %3 %4
pause
