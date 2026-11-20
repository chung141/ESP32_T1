# ESP32-S3 + 노트북 웹페이지 비밀번호 LED 제어 예제
# MicroPython v1.20 이상 권장

import network
import socket
import time
from machine import Pin

# ===== 사용자 설정 =====
WIFI_SSID = "YOUR_WIFI_NAME"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
GAME_PASSWORD = "1234"       # 게임 비밀번호: 원하는 값으로 변경
LED_PIN = 38                 # 외부 LED GPIO 번호
# ======================

led = Pin(LED_PIN, Pin.OUT)
led.value(0)


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Wi-Fi 연결 중...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep(1)
    if not wlan.isconnected():
        raise RuntimeError("Wi-Fi 연결 실패: SSID와 비밀번호를 확인하세요.")
    print("Wi-Fi 연결됨")
    print("ESP32 주소:", wlan.ifconfig()[0])
    return wlan


def decode_form_value(value):
    # application/x-www-form-urlencoded 값의 +와 %HH를 해석
    value = value.replace(b"+", b" ")
    result = bytearray()
    i = 0
    while i < len(value):
        if value[i:i + 1] == b"%" and i + 2 < len(value):
            try:
                result.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        result.append(value[i])
        i += 1
    return result.decode("utf-8")


def parse_form(body):
    values = {}
    for item in body.split(b"&"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            values[decode_form_value(key)] = decode_form_value(value)
    return values


def reply(client, status, payload):
    body = payload.encode("utf-8")
    header = (
        "HTTP/1.1 " + status + "\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "Content-Length: " + str(len(body)) + "\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Methods: POST, OPTIONS\r\n"
        "Access-Control-Allow-Headers: Content-Type\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8")
    client.send(header + body)


def read_request(client):
    # 헤더 끝까지 읽고, Content-Length만큼 본문을 받습니다.
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = client.recv(1)
        if not chunk:
            return None, None, b""
        data.extend(chunk)
        if len(data) > 4096:
            return None, None, b""
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("utf-8").split("\r\n")
    first = lines[0].split()
    if len(first) < 2:
        return None, None, b""
    method, path = first[0], first[1]
    content_length = 0
    for line in lines[1:]:
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
    while len(body) < content_length:
        chunk = client.recv(content_length - len(body))
        if not chunk:
            break
        body.extend(chunk)
    return method, path, bytes(body[:content_length])


def run_server():
    connect_wifi()
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 80))
    server.listen(3)
    print("대기 중: 노트북 HTML에 위 ESP32 주소를 입력하세요.")

    while True:
        client, address = server.accept()
        try:
            client.settimeout(5)
            method, path, body = read_request(client)
            if method == "OPTIONS":
                reply(client, "204 No Content", "")
            elif method == "POST" and path == "/control":
                values = parse_form(body)
                password = values.get("password", "")
                action = values.get("action", "")
                if password != GAME_PASSWORD:
                    reply(client, "200 OK", '{"ok":false,"message":"비밀번호가 틀렸습니다."}')
                elif action == "unlock":
                    led.value(1)
                    print("정답! LED 켜짐", address)
                    reply(client, "200 OK", '{"ok":true,"message":"정답입니다! LED가 켜졌습니다."}')
                elif action == "lock":
                    led.value(0)
                    print("LED 꺼짐", address)
                    reply(client, "200 OK", '{"ok":true,"message":"LED를 껐습니다."}')
                else:
                    reply(client, "400 Bad Request", '{"ok":false,"message":"요청을 확인하세요."}')
            else:
                reply(client, "404 Not Found", '{"ok":false,"message":"주소를 찾을 수 없습니다."}')
        except Exception as exc:
            print("요청 처리 오류:", exc)
        finally:
            try:
                client.close()
            except Exception:
                pass


run_server()
