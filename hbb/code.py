# HBB stream controller - code.py
#
# BigTreeTech HBB V1.0 (RP2040, 7 keys, 7 NeoPixels) re-flashed with CircuitPython.
# Each key types F13-F19, which no game uses; OBS's global hotkeys catch them
# even inside a full-screen game. The PC dashboard (PCDash) sends OBS status
# down the USB "data" serial port so each key's light shows what it controls.
#
# Pins come from BigTreeTech's sample-bigtreetech-hbb.cfg: keys are active-low.
#
# The user's own key numbering runs the other way round from BigTreeTech's pin
# order, and OBS was bound to match it (2026-11-10). Roles therefore follow the
# F-key each pin sends, not the pin's position in the list:
#
#   user key  pin   sends  OBS action (as bound)          light
#   1         GP12  F19    Go live / end   (HOLD 1 s)     red = LIVE
#   2         GP13  F18    Record on / off                red pulse = REC, amber = paused
#   3         GP27  F15    Replay buffer (clips)          green = clips ready
#   4         GP19  F16    Mic mute toggle                green = mic live, red = muted
#   5         GP18  F17    Show / hide a source           blue = that source shown
#   6 / 7     GP25 / GP26  F13 / F14  free (e.g. scenes)  cyan = that scene is on air
import math
import time

import board
import digitalio
import neopixel_write
import usb_cdc
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

PINS = [board.GP25, board.GP26, board.GP27, board.GP19, board.GP18, board.GP13, board.GP12]
KEYS = [Keycode.F13, Keycode.F14, Keycode.F15, Keycode.F16, Keycode.F17, Keycode.F18, Keycode.F19]
# What each F-key does (matches the OBS bindings), and so how its light behaves.
ROLE_BY_KEY = {Keycode.F19: "live", Keycode.F18: "rec", Keycode.F15: "replay",
               Keycode.F16: "mic", Keycode.F17: "source", Keycode.F13: "sceneX", Keycode.F14: "sceneY"}
ROLES = [ROLE_BY_KEY.get(k, "") for k in KEYS]
# Going live must never happen from a brush of the hand: hold that key for 1 s.
HOLD_TO_FIRE = {i: 1.0 for i, r in enumerate(ROLES) if r == "live"}
LED_PIN = board.GP20
BRIGHT = 0.35                # 0..1 - these are bright LEDs right next to your eyes
IDLE = (6, 6, 8)             # faint glow so you can find the keys in the dark

kbd = None
for _ in range(20):          # Windows can take a moment to enumerate the HID device
    try:
        kbd = Keyboard(usb_hid.devices)
        break
    except OSError:
        time.sleep(0.25)

buttons = []
for p in PINS:
    b = digitalio.DigitalInOut(p)
    b.switch_to_input(pull=digitalio.Pull.UP)
    buttons.append(b)

led = digitalio.DigitalInOut(LED_PIN)
led.direction = digitalio.Direction.OUTPUT
pixels = bytearray(3 * len(PINS))


def show(colors):
    for i, (r, g, b) in enumerate(colors):
        # GRB order on the wire.
        pixels[3 * i] = int(g * BRIGHT)
        pixels[3 * i + 1] = int(r * BRIGHT)
        pixels[3 * i + 2] = int(b * BRIGHT)
    neopixel_write.neopixel_write(led, pixels)


# --- startup sweep, so a successful flash is obvious ---
for i in range(len(PINS)):
    show([(0, 120, 255) if j == i else (0, 0, 0) for j in range(len(PINS))])
    time.sleep(0.07)

# --- status from the PC ---
status = {}
status_at = -100.0
rx = b""      # bytes, reassigned - CircuitPython bytearrays can't delete slices
serial = usb_cdc.data


def read_status():
    """Lines like: L1 R0 P0 B1 M0 C1 X1 Y0  (one letter per field)."""
    global status_at, rx
    if serial is None or not serial.in_waiting:
        return
    rx += serial.read(serial.in_waiting)
    if len(rx) > 512:        # garbage guard: keep only the newest data
        rx = rx[-128:]
    while b"\n" in rx:
        i = rx.index(b"\n")
        line = rx[:i].decode("utf-8", "ignore").strip()
        rx = rx[i + 1:]
        if not line:
            continue
        new = {}
        for tok in line.split():
            if len(tok) >= 2 and tok[1:].isdigit():
                new[tok[0]] = int(tok[1:])
        status.clear()
        status.update(new)
        status_at = time.monotonic()


def colors(now, held):
    fresh = now - status_at < 5          # PC dashboard talking to us?
    pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(now * 5))
    c = [IDLE] * len(PINS)
    if fresh:
        g = status.get
        for i, role in enumerate(ROLES):
            if role == "live":
                c[i] = (255, 0, 20) if g("L") else IDLE
            elif role == "rec" and g("R"):
                c[i] = (255, 140, 0) if g("P") else (int(255 * pulse), 0, 0)
            elif role == "replay":
                c[i] = (0, 200, 40) if g("B") else IDLE
            elif role == "mic":
                c[i] = (255, 0, 0) if g("M") else (0, 160, 30)
            elif role == "source":
                c[i] = (0, 60, 255) if g("C") else IDLE
            elif role == "sceneX":
                c[i] = (0, 200, 200) if g("X") else IDLE
            elif role == "sceneY":
                c[i] = (0, 200, 200) if g("Y") else IDLE
    # Feedback for the keys being touched right now.
    for i, t0 in held.items():
        if i in HOLD_TO_FIRE:
            frac = min(1.0, (now - t0) / HOLD_TO_FIRE[i])
            c[i] = (255, int(255 * (1 - frac)), int(255 * (1 - frac)))   # white -> red as you hold
        else:
            c[i] = (255, 255, 255)
    return c


held = {}        # key index -> time pressed
fired = set()    # hold-keys already sent during this press
last = [True] * len(PINS)
flash_until = {}

while True:
    now = time.monotonic()
    read_status()
    for i, b in enumerate(buttons):
        v = b.value                      # True = released (active-low)
        if v != last[i]:
            time.sleep(0.005)            # debounce
            v = b.value
        if v == last[i]:
            continue
        last[i] = v
        if not v:                        # pressed
            held[i] = now
            if i not in HOLD_TO_FIRE and kbd:
                kbd.send(KEYS[i])
        else:                            # released
            held.pop(i, None)
            fired.discard(i)
            flash_until[i] = now + 0.15
    for i, t0 in list(held.items()):
        if i in HOLD_TO_FIRE and i not in fired and now - t0 >= HOLD_TO_FIRE[i]:
            if kbd:
                kbd.send(KEYS[i])
            fired.add(i)
    c = colors(now, held)
    for i, t in list(flash_until.items()):
        if now < t and i not in held:
            c[i] = (180, 180, 180)
        elif now >= t:
            del flash_until[i]
    show(c)
    time.sleep(0.01)
