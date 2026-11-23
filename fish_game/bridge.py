"""PC bridge for the ESP32 boat-fishing game (v3).
Runs on the PC with normal Python 3.10-3.13 (NOT on the ESP32).

 - camera: tracks the always-on green LED (rod tip) with OpenCV
     * bright-core + green-halo detection, so an over-exposed (white) LED centre still counts
     * prefers the blob near the last position (ignores other green things in the room)
     * One-Euro smoothing (steady when still, fast when you swing)
     * short dropouts are bridged; fast upward flick of the rod = "flick" event (hook)
 - optional MediaPipe hands: which part of the hand holds the LED
 - polls the ESP32 /state (sensors) and forwards beeps to the ESP32 buzzer
 - serves index.html + /state + /cam.jpg on http://localhost:8000
"""
import argparse
import json
import math
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, build_opener, ProxyHandler

# ESP32 is on the local network: never send its requests through a Windows/system proxy
ESP_OPENER = build_opener(ProxyHandler({}))

try:
    import cv2
    import numpy as np
except ImportError:
    raise SystemExit('OpenCV required: python -m pip install opencv-python numpy')

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / 'hand_landmarker.task'
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/'
             'hand_landmarker/float16/1/hand_landmarker.task')

STATE = {'camera': False, 'cam_error': '', 'cam_fps': 0,
         'led_found': False, 'led_x': 0.5, 'led_y': 0.5, 'led_raw_x': 0.5, 'led_raw_y': 0.5,
         'led_quality': 0.0, 'flick': 0, 'chop': 0,
         'mediapipe': 'off', 'hand_found': False, 'holding': False, 'grip': '',
         'esp_connected': False, 'esp_error': '', 'esp_url': '', 'board': '',
         'x': 0.5, 'y': 0.5, 'water': 0, 'tilt': False, 'tilt_events': 0,
         'reel': 0, 'presses': 0, 'button': False, 'ms': 0}
LOCK = threading.Lock()
JPEG = {'data': b''}
ESP = {'base': ''}
HAND_LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
              (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
              (17, 18), (18, 19), (19, 20)]


def set_state(**kw):
    with LOCK:
        STATE.update(kw)


# ---------------- MediaPipe (old "solutions" API or new Tasks API) ----------------
class HandDetector:
    def __init__(self):
        self.kind, self.impl, self.t0 = 'off', None, time.monotonic()
        try:
            import mediapipe as mp
        except Exception as err:
            print('[MediaPipe] not installed -> LED-only mode (%s)' % err)
            return
        self.mp = mp
        if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'hands'):
            self.impl = mp.solutions.hands.Hands(max_num_hands=2, min_detection_confidence=0.55,
                                                 min_tracking_confidence=0.5)
            self.kind = 'solutions'
        else:
            try:
                from mediapipe.tasks.python import vision, BaseOptions
                if not MODEL.exists():
                    print('[MediaPipe] downloading hand model (~7.8 MB, first run only)...')
                    with urlopen(MODEL_URL, timeout=20) as r:
                        data = r.read()
                    MODEL.write_bytes(data)
                opts = vision.HandLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(MODEL)),
                    running_mode=vision.RunningMode.VIDEO, num_hands=2,
                    min_hand_detection_confidence=0.5, min_tracking_confidence=0.5)
                self.impl = vision.HandLandmarker.create_from_options(opts)
                self.kind = 'tasks'
            except Exception as err:
                print('[MediaPipe] Tasks API failed -> LED-only mode:', err)
                if MODEL.exists() and MODEL.stat().st_size < 100000:
                    MODEL.unlink()
                return
        print('[MediaPipe] hand tracking ON (%s API)' % self.kind)

    def process(self, frame_bgr):
        if self.impl is None:
            return []
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        if self.kind == 'solutions':
            res = self.impl.process(rgb)
            return [[(p.x, p.y) for p in h.landmark] for h in (res.multi_hand_landmarks or [])]
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        res = self.impl.detect_for_video(img, int((time.monotonic() - self.t0) * 1000))
        return [[(p.x, p.y) for p in h] for h in (res.hand_landmarks or [])]


# ---------------- Green LED tracker ----------------
class OneEuro:
    """One-Euro filter: low jitter when slow, low lag when fast."""
    def __init__(self, min_cutoff=1.4, beta=8.0, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _a(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(1e-3, t - self.t)
        self.t = t
        dx = (x - self.x) / dt
        a_d = self._a(self.dc, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        a = self._a(self.mc + self.beta * abs(self.dx), dt)
        self.x = a * x + (1 - a) * self.x
        return self.x


class LedTracker:
    HOLD_S = 0.35          # keep last position this long when the LED briefly disappears

    def __init__(self):
        self.fx, self.fy = OneEuro(), OneEuro()
        self.last = None       # last raw (x, y) normalized
        self.last_seen = 0.0
        self.pos = (0.5, 0.5)
        self.hist = deque(maxlen=12)
        self.flicks = 0
        self.last_flick = 0.0
        self.chops = 0
        self.last_chop = 0.0

    @staticmethod
    def candidates(frame):
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        b, g, r = [frame[..., i].astype(np.int16) for i in range(3)]
        # (1) green halo: saturated green and fairly bright
        halo = ((hue >= 35) & (hue <= 90) & (sat >= 60) & (val >= 110)).astype(np.uint8)
        # (2) LED core: blown-out bright pixels that are still green-dominant
        core = ((val >= 200) & (g >= r + 8) & (g >= b) & (g >= 200)).astype(np.uint8)
        # core pixels only count if green halo is nearby (rules out white lamps / windows)
        near = cv2.dilate(halo, np.ones((9, 9), np.uint8))
        mask = (halo | (core & near)) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in contours:
            area = cv2.contourArea(c)
            if not 8 <= area <= w * h * 0.03:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            aspect = max(bw, bh) / max(1, min(bw, bh))
            if aspect > 3:
                continue
            roi = val[y:y + bh, x:x + bw]
            peak = float(roi.max()) / 255
            fill = area / max(1, bw * bh)
            (cx, cy), rad = cv2.minEnclosingCircle(c)
            # bright, compact, roundish blobs score highest
            score = (peak ** 3) * math.sqrt(area) * (0.5 + fill) / aspect
            out.append({'x': cx / w, 'y': cy / h, 'r': rad, 'score': score, 'peak': peak})
        return out

    def update(self, frame, now):
        cands = self.candidates(frame)
        best = None
        if cands:
            for c in cands:
                if self.last is not None and now - self.last_seen < 1.0:
                    d = math.hypot(c['x'] - self.last[0], c['y'] - self.last[1])
                    c['score'] *= 0.35 + math.exp(-(d / 0.18) ** 2)   # stick to the tracked LED
            best = max(cands, key=lambda c: c['score'])
            if best['peak'] < 0.8:          # a lit LED is near the top of the camera's range
                best = None
        if best:
            self.last, self.last_seen = (best['x'], best['y']), now
            self.pos = (self.fx(best['x'], now), self.fy(best['y'], now))
            self.hist.append((now, best['y']))
            self._motion(now)
            quality = min(1.0, best['peak'] * min(1.0, best['r'] / 6))
            return True, best, quality
        found = now - self.last_seen < self.HOLD_S
        return found, None, 0.0

    def _motion(self, now):
        """Fast vertical moves of the LED.
        up   (image y decreases) -> 'flick' = hook the fish
        down (image y increases) -> 'chop'  = knife cut in the cleaning game"""
        if len(self.hist) < 3:
            return
        t1, y1 = self.hist[-1]
        for t0, y0 in self.hist:
            if not 0.04 <= t1 - t0 <= 0.25:
                continue
            if y0 - y1 > 0.13 and now - self.last_flick >= 0.7:
                self.flicks += 1
                self.last_flick = now
                self.hist.clear()
                return
            if y1 - y0 > 0.10 and now - self.last_chop >= 0.22:
                self.chops += 1
                self.last_chop = now
                self.hist.clear()
                return


def grip_label(point, hands):
    if not point or not hands:
        return False, ''
    names = {0: '손목', 4: '엄지', 8: '검지 끝', 9: '손바닥 중앙', 12: '중지 끝', 17: '새끼손가락'}
    best, dist = '', 9
    for hand in hands:
        for idx, name in names.items():
            d = math.hypot(point[0] - hand[idx][0], point[1] - hand[idx][1])
            if d < dist:
                best, dist = name, d
    size = max(math.hypot(h[0][0] - h[9][0], h[0][1] - h[9][1]) for h in hands)
    holding = dist < max(0.08, size * 1.6)
    return holding, (best if holding else '')


def open_camera(index, exposure):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == 'win32' else cv2.VideoCapture(index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        if exposure is not None:              # darker image -> LED stands out much better
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if sys.platform != 'win32' else 0)
            cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
    return cap


def cam_worker(index, mirror=True, exposure=None):
    hands = HandDetector()
    set_state(mediapipe=hands.kind)
    tracker = LedTracker()
    landmarks, frame_no = [], 0
    while True:
        cap = open_camera(index, exposure)
        if not cap.isOpened():
            set_state(camera=False, cam_error='카메라 %d번을 열 수 없음 (다른 앱 사용 중? --camera 1 시도)' % index)
            time.sleep(3)
            continue
        set_state(camera=True, cam_error='')
        print('[Camera] ready (index %d). Show the green LED to the camera.' % index)
        fails, fps_t, fps_n = 0, time.monotonic(), 0
        while fails < 30:
            ok, frame = cap.read()
            if not ok:
                fails += 1
                time.sleep(0.05)
                continue
            fails = 0
            now = time.monotonic()
            if mirror:
                frame = cv2.flip(frame, 1)
            if frame.shape[1] > 800:
                frame = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])))
            h, w = frame.shape[:2]
            found, blob, quality = tracker.update(frame, now)
            frame_no += 1
            if hands.impl is not None and frame_no % 3 == 0:     # hands are slow: every 3rd frame
                try:
                    landmarks = hands.process(frame)
                except Exception:
                    landmarks = []
            point = tracker.pos if found else None
            holding, grip = grip_label(point, landmarks)
            # preview overlay
            for hand in landmarks:
                for a, b in HAND_LINKS:
                    cv2.line(frame, (int(hand[a][0] * w), int(hand[a][1] * h)),
                             (int(hand[b][0] * w), int(hand[b][1] * h)), (255, 200, 80), 2)
            if point:
                px, py = int(point[0] * w), int(point[1] * h)
                col = (80, 255, 80) if blob else (0, 200, 255)
                cv2.circle(frame, (px, py), 18, col, 3)
                cv2.line(frame, (px - 28, py), (px - 12, py), col, 2)
                cv2.line(frame, (px + 12, py), (px + 28, py), col, 2)
            small = cv2.resize(frame, (320, int(320 * h / w)))
            ok_j, jpg = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok_j:
                JPEG['data'] = jpg.tobytes()
            fps_n += 1
            with LOCK:
                STATE.update(camera=True, led_found=bool(found), hand_found=bool(landmarks),
                             holding=holding, grip=grip, led_quality=round(quality, 2),
                             flick=tracker.flicks, chop=tracker.chops)
                if found:
                    STATE['led_x'], STATE['led_y'] = round(tracker.pos[0], 4), round(tracker.pos[1], 4)
                if blob:
                    STATE['led_raw_x'], STATE['led_raw_y'] = round(blob['x'], 4), round(blob['y'], 4)
                if now - fps_t >= 1:
                    STATE['cam_fps'] = fps_n
                    fps_t, fps_n = now, 0
            time.sleep(0.002)
        cap.release()
        set_state(camera=False, cam_error='카메라 신호 끊김, 재연결 중...')


# ---------------- ESP32 ----------------
def esp_worker(url):
    fails, was_connected = 0, False
    while True:
        try:
            with ESP_OPENER.open(url, timeout=1.5) as response:
                reading = json.load(response)
            with LOCK:
                for key in ('x', 'y', 'water', 'tilt', 'tilt_events', 'reel', 'presses',
                            'button', 'ms', 'board'):
                    if key in reading:
                        STATE[key] = reading[key]
                STATE['esp_connected'] = True
                STATE['esp_error'] = ''
            fails = 0
            if not was_connected:
                print('[ESP32] connected:', url)
                was_connected = True
                esp_beep()
        except Exception as err:
            fails += 1
            if fails == 3 or fails % 100 == 0:
                print('[ESP32] no response from %s -> %s' % (url, err))
            if fails >= 3:
                set_state(esp_connected=False, esp_error=str(err)[:80])
                if was_connected:
                    print('[ESP32] connection lost:', err)
                was_connected = False
        time.sleep(0.03)


def esp_beep():
    def go():
        try:
            ESP_OPENER.open(ESP['base'] + '/beep', timeout=1).read()
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            content, kind = (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8'
        elif path == '/state':
            with LOCK:
                content = json.dumps(STATE).encode('utf8')
            kind = 'application/json; charset=utf-8'
        elif path == '/cam.jpg':
            if not JPEG['data']:
                self.send_response(204); self.end_headers(); return
            content, kind = JPEG['data'], 'image/jpeg'
        elif path == '/espbeep':
            esp_beep()
            content, kind = b'ok', 'text/plain'
        elif path == '/favicon.ico':
            self.send_response(204); self.end_headers(); return
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        pass


def normalize_esp(value):
    """Accept '172.30.1.93', '172.30.1.20:8081' or a full URL."""
    v = value.strip()
    if not v.startswith('http'):
        v = 'http://' + v
    host = v.split('//', 1)[1].split('/', 1)[0]
    if ':' not in host:
        host += ':8081'
    return 'http://%s/state' % host


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--esp', default='172.30.1.93', help='ESP32 IP or /state URL')
    ap.add_argument('--camera', type=int, default=0)
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--no-mirror', action='store_true')
    ap.add_argument('--exposure', type=float, default=None,
                    help='manual camera exposure, e.g. -6 (Windows) to make the LED stand out')
    args = ap.parse_args()
    esp_url = normalize_esp(args.esp)
    ESP['base'] = esp_url.rsplit('/', 1)[0]
    set_state(esp_url=esp_url)
    threading.Thread(target=cam_worker, args=(args.camera, not args.no_mirror, args.exposure),
                     daemon=True).start()
    threading.Thread(target=esp_worker, args=(esp_url,), daemon=True).start()
    print('Open http://localhost:%d in browser' % args.port)
    print('ESP32 polling:', esp_url)
    try:
        ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print('Stopped.')
