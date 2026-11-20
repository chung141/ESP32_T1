ESP32-S3 방탈출 비밀번호 LED 예제

구성 파일
- main.py: ESP32-S3에서 실행할 MicroPython 코드
- index.html: 노트북 브라우저에서 열 웹페이지

1. 배선
ESP32-S3 GPIO38 -> 220~1kΩ 저항 -> LED 긴 다리(+) 
LED 짧은 다리(-) -> ESP32-S3 GND
외부 LED를 쓸 때만 이 배선이 필요합니다. LED 극성을 지키세요.

2. main.py 수정
WIFI_SSID, WIFI_PASSWORD를 실제 Wi-Fi 이름과 비밀번호로 바꾸세요.
GAME_PASSWORD를 게임 비밀번호로 바꾸세요.
LED_PIN은 실제 LED 연결 핀으로 맞추세요. 기본은 GPIO38입니다.

3. ESP32-S3에 실행
Thonny에서 main.py를 ESP32-S3에 저장하고 실행합니다.
실행 창에 표시되는 ESP32 주소를 적어 둡니다.

4. 노트북에서 실행
index.html을 노트북으로 복사해 Chrome/Edge에서 엽니다.
ESP32 주소와 게임 비밀번호를 입력하고 [정답 확인]을 누릅니다.
맞으면 LED가 켜지고 [LED 끄기]를 누르면 꺼집니다.
노트북과 ESP32-S3는 같은 Wi-Fi에 연결해야 합니다.

문제가 생기면
- ESP32 주소를 다시 확인하고 두 기기가 같은 Wi-Fi인지 확인합니다.
- 공유기에서 무선 기기 간 통신 차단(AP/client isolation)이 켜져 있으면 끕니다.
- 브라우저가 로컬 네트워크 접근을 물으면 허용합니다.
- GPIO38 대신 다른 핀을 썼다면 main.py의 LED_PIN을 바꿉니다.

교육용 예제 주의
이 예제는 같은 Wi-Fi 안에서 동작하는 연습용입니다. 비밀번호가 HTTP로 전송되므로 실제 출입문이나 민감한 장치의 잠금 제어에는 사용하지 마세요. 인터넷에 포트포워딩하지 마세요.

192.168.0.75
