"""ESCAPE / 32 - MicroPython ESP32-S3 local web game server."""
import time
import json
import socket
import os
import network
from machine import Pin
from config import WIFI_SSID, WIFI_PASSWORD, LED_ACTIVE_HIGH

PINS = {'red': Pin(15, Pin.OUT), 'green': Pin(16, Pin.OUT), 'yellow': Pin(17, Pin.OUT)}
ANSWERS = ['314', 'GYRG', '7314']
SIGNAL = ['green', 'yellow', 'red', 'green']
game = {'started': False, 'stage': 0, 'won': False, 'mistakes': 0}
started_at = 0
penalty = 0
effect_until = 0
signal_at = None
won_remaining = 0


def remaining():
    if game['won']:
        return won_remaining
    if not game['started']:
        return 900
    return max(0, 900 - time.ticks_diff(time.ticks_ms(), started_at) // 1000 - penalty)


def snapshot():
    return dict(game, remaining=remaining(), device='ESP32', signal=SIGNAL if game['stage'] >= 1 else [])


def lights(color):
    for name, pin in PINS.items():
        pin.value(int((name == color) == LED_ACTIVE_HIGH))


def update_leds():
    global signal_at
    now = time.ticks_ms()
    if game['won']:
        lights('green')
    elif game['started'] and remaining() <= 0:
        lights('red')
    elif signal_at is not None:
        elapsed = time.ticks_diff(now, signal_at)
        if elapsed >= 4000:
            signal_at = None
            lights('yellow')
        else:
            lights(SIGNAL[elapsed // 1000] if elapsed % 1000 < 650 else None)
    elif time.ticks_diff(effect_until, now) > 0:
        lights('red')
    else:
        lights('yellow' if game['started'] else None)


def action(path, data):
    global started_at, penalty, effect_until, signal_at, won_remaining
    if path == '/api/start':
        game.update(started=True, stage=0, won=False, mistakes=0)
        started_at = time.ticks_ms()
        penalty = 0
        effect_until = started_at
        signal_at = None
        return dict(snapshot(), ok=True)
    if not game['started'] or remaining() <= 0 or game['won']:
        return dict(snapshot(), ok=False, message='종료된 작전입니다. 새 작전을 시작하세요.')
    if path == '/api/signal':
        if game['stage'] != 1:
            return dict(snapshot(), ok=False, message='통신실 단계에서 사용할 수 있습니다.')
        signal_at = time.ticks_ms()
        return dict(snapshot(), ok=True)
    if path == '/api/solve':
        stage = data.get('stage')
        if type(stage) is not int or stage != game['stage'] or stage >= 3:
            return dict(snapshot(), ok=False, message='현재 단계와 일치하지 않습니다.')
        if str(data.get('answer', '')).strip().upper() == ANSWERS[stage]:
            game['stage'] += 1
            signal_at = None
            return dict(snapshot(), ok=True)
        game['mistakes'] += 1
        penalty += 20
        signal_at = None
        effect_until = time.ticks_add(time.ticks_ms(), 1500)
        return dict(snapshot(), ok=False, message='인증 실패. 제한 시간 20초 차감.')
    if path == '/api/exit':
        if game['stage'] == 3:
            won_remaining = remaining()
            game['won'] = True
            return dict(snapshot(), ok=True)
        return dict(snapshot(), ok=False, message='세 개의 보안 장치를 먼저 해제하세요.')
    return dict(snapshot(), ok=False, message='알 수 없는 요청입니다.')


def send_all(conn, data):
    data = memoryview(data)
    while data:
        sent = conn.send(data)
        if not sent:
            raise OSError('connection closed')
        data = data[sent:]


def reply(conn, body, status='200 OK', content_type='application/json; charset=utf-8'):
    if not isinstance(body, bytes):
        body = body.encode('utf-8')
    header = 'HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n' % (status, content_type, len(body))
    send_all(conn, header.encode())
    send_all(conn, body)


def serve_client(conn):
    conn.settimeout(2)
    request = b''
    while b'\r\n\r\n' not in request:
        chunk = conn.recv(512)
        if not chunk:
            return
        request += chunk
        if len(request) > 4096:
            reply(conn, '{}', '413 Payload Too Large')
            return
    headers, body = request.split(b'\r\n\r\n', 1)
    lines = headers.decode().split('\r\n')
    method, target, _ = lines[0].split(' ')
    path = target.split('?')[0]
    if method == 'GET' and path == '/':
        size = os.stat('index.html')[6]
        send_all(conn, ('HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n' % size).encode())
        with open('index.html', 'rb') as page:
            while True:
                chunk = page.read(1024)
                if not chunk:
                    break
                send_all(conn, chunk)
        return
    if method == 'GET' and path == '/api/state':
        reply(conn, json.dumps(snapshot()))
        return
    allowed = ('/api/start', '/api/solve', '/api/signal', '/api/exit')
    if method != 'POST' or path not in allowed:
        reply(conn, '{}', '404 Not Found')
        return
    length = 0
    content_type = ''
    for line in lines[1:]:
        key, _, value = line.partition(':')
        if key.lower() == 'content-length':
            length = int(value.strip())
        if key.lower() == 'content-type':
            content_type = value.strip().lower()
    if length < 0 or length > 512 or not content_type.startswith('application/json'):
        reply(conn, '{}', '400 Bad Request')
        return
    while len(body) < length:
        chunk = conn.recv(length - len(body))
        if not chunk:
            return
        body += chunk
    data = json.loads(body[:length] or b'{}')
    if not isinstance(data, dict):
        reply(conn, '{}', '400 Bad Request')
        return
    reply(conn, json.dumps(action(path, data)))


def run():
    lights(None)
    if WIFI_SSID == 'YOUR_WIFI_NAME':
        print('Edit WIFI_SSID and WIFI_PASSWORD in config.py, then restart.')
        return
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(60):
        if wlan.isconnected():
            break
        time.sleep_ms(500)
    if not wlan.isconnected():
        lights('red')
        print('Wi-Fi connection failed. Check 2.4 GHz SSID and password.')
        return
    print('Open in phone browser: http://' + wlan.ifconfig()[0])
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 80))
    server.listen(2)
    server.settimeout(0.05)
    try:
        while True:
            update_leds()
            try:
                conn, addr = server.accept()
            except OSError:
                continue
            try:
                serve_client(conn)
            except (OSError, ValueError, KeyError) as exc:
                print('Request error:', type(exc).__name__)
            finally:
                conn.close()
    finally:
        lights(None)
        server.close()


if __name__ == '__main__':
    run()
