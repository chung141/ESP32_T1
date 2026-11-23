# ESP32 / ESP32-S3 MicroPython (Thonny): save to the board as main.py
# v2: encoder & button use interrupts (no lost steps), board auto pin map,
#     Wi-Fi auto-reconnect, event counters so short taps are never missed.
import network, socket, time, json, os
from machine import Pin, ADC, PWM

# ---- 1) Change these two lines. ESP32 supports 2.4 GHz Wi-Fi only. ----
WIFI_SSID = 'YOUR_WIFI_NAME'
WIFI_PASSWORD = 'YOUR_WIFI_PASSWORD'
PORT = 8081

# ---- 2) Pins: chosen automatically from the board type. ----
# All analog inputs use ADC1 pins (ADC2 does not work while Wi-Fi is on).
BOARD = os.uname().machine
IS_S3 = 'S3' in BOARD
if IS_S3:   # ESP32-S3 DevKit
    PIN_JOY_X, PIN_JOY_Y, PIN_WATER = 1, 2, 4
    PIN_TILT = 5
    PIN_ENC_A, PIN_ENC_B, PIN_ENC_SW = 6, 7, 8
    PIN_LED, PIN_BUZZER = 10, 11
else:       # classic ESP32 (WROOM / DevKitC). GPIO 6-11 are flash pins: never use them.
    PIN_JOY_X, PIN_JOY_Y, PIN_WATER = 34, 35, 32
    PIN_TILT = 13
    PIN_ENC_A, PIN_ENC_B, PIN_ENC_SW = 25, 26, 27
    PIN_LED, PIN_BUZZER = 23, 18

joy_x = ADC(Pin(PIN_JOY_X))
joy_y = ADC(Pin(PIN_JOY_Y))
water = ADC(Pin(PIN_WATER))
for adc in (joy_x, joy_y, water):
    try:
        adc.atten(ADC.ATTN_11DB)      # full 0-3.3 V range
    except AttributeError:
        pass

tilt = Pin(PIN_TILT, Pin.IN, Pin.PULL_UP)
enc_a = Pin(PIN_ENC_A, Pin.IN, Pin.PULL_UP)
enc_b = Pin(PIN_ENC_B, Pin.IN, Pin.PULL_UP)
enc_sw = Pin(PIN_ENC_SW, Pin.IN, Pin.PULL_UP)
led = Pin(PIN_LED, Pin.OUT, value=1)    # green LED on = camera marker
buzzer = PWM(Pin(PIN_BUZZER), freq=1500, duty_u16=0)

# ---- Interrupt handlers: keep them tiny ----
reel = 0            # encoder detent count (signed)
presses = 0         # encoder push-button presses
tilt_events = 0     # tilt switch changes
_last = {'a': 0, 'sw': 0, 'tilt': 0}


def _enc_irq(pin):
    global reel
    now = time.ticks_ms()
    if time.ticks_diff(now, _last['a']) < 2:       # debounce
        return
    _last['a'] = now
    if enc_a.value() == 0:                          # count on falling edge of A
        reel += 1 if enc_b.value() else -1


def _sw_irq(pin):
    global presses
    now = time.ticks_ms()
    if time.ticks_diff(now, _last['sw']) > 150:
        _last['sw'] = now
        presses += 1


def _tilt_irq(pin):
    global tilt_events
    now = time.ticks_ms()
    if time.ticks_diff(now, _last['tilt']) > 120:
        _last['tilt'] = now
        tilt_events += 1


enc_a.irq(handler=_enc_irq, trigger=Pin.IRQ_FALLING)
enc_sw.irq(handler=_sw_irq, trigger=Pin.IRQ_FALLING)
tilt.irq(handler=_tilt_irq, trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING)


def beep(freq=1200, duration_ms=60):
    try:
        buzzer.freq(freq)
        buzzer.duty_u16(9000)
        time.sleep_ms(duration_ms)
    finally:
        buzzer.duty_u16(0)


def sample():
    return {
        'x': round(joy_x.read_u16() / 65535, 3),
        'y': round(joy_y.read_u16() / 65535, 3),
        'water': round(water.read_u16() / 65535, 3),
        'tilt': tilt.value() == 0,
        'tilt_events': tilt_events,
        'reel': reel,
        'presses': presses,
        'button': enc_sw.value() == 0,
        'ms': time.ticks_ms(),
        'board': 'S3' if IS_S3 else 'ESP32',
    }


wlan = network.WLAN(network.STA_IF)


def connect_wifi(timeout_ms=20000):
    wlan.active(True)
    if wlan.isconnected():
        return wlan.ifconfig()[0]
    print('Connecting to Wi-Fi:', WIFI_SSID)
    # After a soft reboot (Thonny Run / Ctrl+D) the radio can still be mid-connect,
    # which raises "Wifi Internal State Error". Reset the radio first.
    try:
        wlan.disconnect()
    except OSError:
        pass
    wlan.active(False)
    time.sleep_ms(300)
    wlan.active(True)
    time.sleep_ms(300)
    try:
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    except OSError as err:
        print('Wi-Fi connect error:', err)
        return None
    start = time.ticks_ms()
    while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        led.value(not led.value())              # blink while connecting
        time.sleep_ms(250)
    led.value(1)
    if wlan.isconnected():
        return wlan.ifconfig()[0]
    return None


def main():
    print('Board:', BOARD)
    ip = None
    while not ip:
        ip = connect_wifi()
        if not ip:
            print('Wi-Fi failed. Check 2.4GHz / SSID / password. Retrying in 3 s...')
            beep(400, 300)
            time.sleep(3)
    url = 'http://%s:%d/state' % (ip, PORT)
    print('=' * 52)
    print('ESP32 IP :', ip)
    print('Test URL :', url)
    print('PC run   : launch.bat %s   (or ./launch.run %s)' % (ip, ip))
    print('=' * 52)
    beep(1650, 90)

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(socket.getaddrinfo('0.0.0.0', PORT)[0][-1])
    server.listen(4)
    server.settimeout(0.5)
    last_check = time.ticks_ms()
    while True:
        if time.ticks_diff(time.ticks_ms(), last_check) > 3000:
            last_check = time.ticks_ms()
            if not wlan.isconnected():
                print('Wi-Fi lost, reconnecting...')
                connect_wifi()
        try:
            client, _ = server.accept()
        except OSError:
            continue
        try:
            client.settimeout(1)
            request = client.recv(512)
            parts = request.split(b' ')
            path = parts[1] if len(parts) > 1 else b'/'
            if path.startswith(b'/state'):
                body = json.dumps(sample()).encode()
                ctype = b'application/json'
            elif path.startswith(b'/beep'):
                beep(1800, 50)
                body, ctype = b'ok', b'text/plain'
            else:
                body = b'ESP32 fishing server OK. Use /state'
                ctype = b'text/plain; charset=utf-8'
            client.send(b'HTTP/1.0 200 OK\r\nContent-Type: ' + ctype +
                        b'\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: ' +
                        str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n')
            client.send(body)
        except OSError:
            pass
        finally:
            client.close()


try:
    main()
except KeyboardInterrupt:
    led.off()
    buzzer.deinit()
    print('Stopped')
