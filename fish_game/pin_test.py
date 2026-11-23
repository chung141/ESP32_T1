# ESP32-S3 배선 점검용 (Thonny에서 그냥 실행. main.py로 저장하지 마세요)
# 센서를 하나씩 움직이면서 값이 바뀌는지 확인합니다. 멈추려면 Thonny의 Stop(Ctrl+C).
import time, os
from machine import Pin, ADC, PWM

S3 = 'S3' in os.uname().machine
if S3:
    JX, JY, WATER, TILT, CLK, DT, SW, LED, BUZ = 1, 2, 4, 5, 6, 7, 8, 10, 11
else:
    JX, JY, WATER, TILT, CLK, DT, SW, LED, BUZ = 34, 35, 32, 13, 25, 26, 27, 23, 18
print('보드:', os.uname().machine)
print('핀: 조이스틱X=%d Y=%d 수위=%d 기울기=%d 엔코더CLK=%d DT=%d SW=%d LED=%d 부저=%d'
      % (JX, JY, WATER, TILT, CLK, DT, SW, LED, BUZ))

# 1) LED 3번 깜빡임
led = Pin(LED, Pin.OUT)
for _ in range(3):
    led.on(); time.sleep(0.25); led.off(); time.sleep(0.25)
led.on()
print('[LED] 3번 깜빡이고 켜져 있어야 정상')

# 2) 부저 (연결 안 했으면 무시)
buz = PWM(Pin(BUZ), freq=1500, duty_u16=9000); time.sleep(0.15); buz.duty_u16(0)
print('[부저] 삑 소리 1번 (부저가 없으면 무시)')

adcs = {}
for name, p in (('X', JX), ('Y', JY), ('수위', WATER)):
    a = ADC(Pin(p))
    try:
        a.atten(ADC.ATTN_11DB)
    except AttributeError:
        pass
    adcs[name] = a
tilt = Pin(TILT, Pin.IN, Pin.PULL_UP)
clk = Pin(CLK, Pin.IN, Pin.PULL_UP)
dt = Pin(DT, Pin.IN, Pin.PULL_UP)
sw = Pin(SW, Pin.IN, Pin.PULL_UP)

count = 0
def _irq(p):
    global count
    if clk.value() == 0:
        count += 1 if dt.value() else -1
clk.irq(handler=_irq, trigger=Pin.IRQ_FALLING)

print()
print('조이스틱: 가만히 0.45~0.55 / 끝까지 밀면 0.0 근처 또는 1.0 근처')
print('엔코더: 돌리면 릴 숫자가 1씩 변함 / 누르면 버튼=눌림')
print('기울기: 기울이면 0↔1 바뀜')
print('-' * 60)
while True:
    x = adcs['X'].read_u16() / 65535
    y = adcs['Y'].read_u16() / 65535
    w = adcs['수위'].read_u16() / 65535
    print('X=%.2f Y=%.2f 수위=%.2f | 기울기=%d | 릴=%4d 버튼=%s'
          % (x, y, w, tilt.value(), count, '눌림' if sw.value() == 0 else '-'))
    time.sleep(0.3)
