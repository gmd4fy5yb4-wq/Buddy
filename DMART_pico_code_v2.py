import board, busio, time
import digitalio
import analogio
import sdcardio, storage
import audiobusio, audiocore
import alarm
import random
import microcontroller

# =========================================================
# SETTINGS
# =========================================================
LCD_COLS = 20
LCD_ROWS = 4
I2C_ADDR = 0x27

SOUNDS_DIR = "/sd/Sounds"        # case-sensitive
SLEEP_AFTER_S = 300              # 5 min idle -> sleep
SLEEP_MESSAGE_S = 2.0            # hold "Sleeping..." before pulsing starts
SLEEP_PULSE_S = 2.5              # backlight pulse cycle while asleep (seconds)

COOLDOWN_S = 1.0                 # post-play cooldown
RELEASE_REQUIRED = True          # must release before retrigger
TOUCH_DEBOUNCE_MS = 80           # pad must stay HIGH this long to register

COUNTDOWN_SHOW_S = 60            # show countdown in last N seconds before sleep
POWER_OFF_HOLD_S = 5             # hold I/O button this long to power off

BOOT_WAV = f"{SOUNDS_DIR}/Boot.wav"

# Battery (Pimoroni Pico LiPo)
BAT_FULL_V = 4.2                 # fully-charged LiPo
BAT_EMPTY_V = 3.2                # practical empty (protects the cell)
BAT_DIVIDER = 3                  # on-board voltage-divider ratio
BAT_UPDATE_S = 30                # seconds between ADC reads
BAT_LOW_PCT = 15                 # show warning below this
BAT_CRIT_PCT = 5                 # auto power-off below this

# Idle chatter
IDLE_CHATTER_S = 60              # rotate text every N seconds idle

# Easter egg
EASTER_EGG_SEQ = [0, 2, 4]      # pad indices for the secret combo
EASTER_EGG_MSG = "You found the secret!"

# Sound-reactive expressions per touch pad
TOUCH_EXPR = ["surprised", "center", "happy", ("center", "wink_shut"), "surprised"]

# Snarky climate messages — shown randomly when a touch pad is pressed
CLIMATE_MSGS = [
    "Planet B? Nope.",
    "Act now or swim later",
    "Cool it. Literally.",
    "Fossil fools beware",
    "The sea is rising...",
    "Earth: Handle w/care",
    "Less talk more trees",
    "Your ice caps called",
    "Compost happens",
    "Skip the straw",
    "Trees > Tweets",
    "Go green or go home",
    "Think global act now",
    "Carbon who? Footwhat",
    "Recycle this thought",
    "Hug a tree today",
    "Be the change. Now.",
    "Save water. Drink tea",
    "Reduce. Reuse. Relax",
    "Earth called. Pick up",
]

# =========================================================
# LCD (PCF8574 backpack)  SDA=GP0  SCL=GP1
# Flicker fix: only redraws when text actually changes
# =========================================================
i2c = busio.I2C(scl=board.GP1, sda=board.GP0, frequency=50000)

ENABLE = 0x04
RS = 0x01
_backlight_on = True

_last_lcd_1 = None
_last_lcd_2 = None

def lcd_reset():
    global _last_lcd_1, _last_lcd_2, _last_eye_state, _loaded_expr_l, _loaded_expr_r, _loaded_mouth
    _last_lcd_1 = None
    _last_lcd_2 = None
    _last_eye_state = None
    _loaded_expr_l = None
    _loaded_expr_r = None
    _loaded_mouth = None
    lcd_init()
    lcd_clear()

def lcd_backlight(on):
    global _backlight_on
    _backlight_on = bool(on)

def _i2c_write(byte_val):
    while not i2c.try_lock():
        pass
    try:
        i2c.writeto(I2C_ADDR, bytes([byte_val]))
    finally:
        i2c.unlock()

def _write_byte(b):
    bl = 0x08 if _backlight_on else 0x00
    _i2c_write(b | bl)

def _pulse_enable(b):
    _write_byte(b | ENABLE)
    time.sleep(0.0005)
    _write_byte(b & ~ENABLE)
    time.sleep(0.0001)

def _lcd_write_nibble(nibble, mode=0):
    b = (nibble & 0xF0) | mode
    _write_byte(b)
    _pulse_enable(b)

def lcd_cmd(cmd):
    _lcd_write_nibble(cmd & 0xF0, 0)
    _lcd_write_nibble((cmd << 4) & 0xF0, 0)

def lcd_data(data):
    _lcd_write_nibble(data & 0xF0, RS)
    _lcd_write_nibble((data << 4) & 0xF0, RS)

def lcd_init():
    time.sleep(0.05)
    _lcd_write_nibble(0x30)
    time.sleep(0.005)
    _lcd_write_nibble(0x30)
    time.sleep(0.001)
    _lcd_write_nibble(0x20)

    lcd_cmd(0x28)  # 2-line, 5x8
    lcd_cmd(0x0C)  # display on, cursor off
    lcd_cmd(0x06)  # entry mode
    lcd_cmd(0x01)  # clear
    time.sleep(0.005)

def lcd_clear():
    lcd_cmd(0x01)
    time.sleep(0.005)

def lcd_set_cursor(row, col):
    offsets = [0x80, 0xC0, 0x94, 0xD4]
    lcd_cmd(offsets[row] + col)

def lcd_print(s):
    for ch in s:
        lcd_data(ord(ch))

def _fit_line(s, width, center=False):
    s = "" if s is None else str(s)
    if len(s) > width:
        return s[:width]
    if center:
        pad_total = width - len(s)
        left = pad_total // 2
        right = pad_total - left
        return (" " * left) + s + (" " * right)
    return s + (" " * (width - len(s)))

def _lcd_line(left, right="", width=LCD_COLS):
    """Left-align *left*, right-align *right*, pad the gap with spaces."""
    if not right:
        return _fit_line(left, width)
    gap = width - len(left) - len(right)
    if gap < 1:
        return (left + " " + right)[:width]
    return left + (" " * gap) + right

def lcd_show(line1="", line2="", center=False):
    global _last_lcd_1, _last_lcd_2
    l1 = _fit_line(line1, LCD_COLS, center=center)
    l2 = _fit_line(line2, LCD_COLS, center=center)

    if l1 == _last_lcd_1 and l2 == _last_lcd_2:
        return

    _last_lcd_1, _last_lcd_2 = l1, l2

    lcd_clear()
    # Center vertically on the 4-row display (rows 1-2)
    lcd_set_cursor(1, 0); lcd_print(l1)
    lcd_set_cursor(2, 0); lcd_print(l2)

def _scroll_frames(s, width):
    s = "" if s is None else str(s)
    if len(s) <= width:
        return [s]
    gap = "   "
    scroll = s + gap
    frames = []
    for i in range(len(scroll)):
        window = (scroll + scroll)[i:i + width]
        frames.append(window)
    return frames

def lcd_show_scroll(line1="", line2="", center=False,
                    step_s=0.18, hold_s=1.0, loops=2):
    f1 = _scroll_frames(line1, LCD_COLS)
    f2 = _scroll_frames(line2, LCD_COLS)

    if len(f1) == 1 and len(f2) == 1:
        lcd_show(line1, line2, center=center)
        time.sleep(hold_s)
        return

    max_len = max(len(f1), len(f2))
    for _ in range(loops):
        for i in range(max_len):
            a = (f1[i % len(f1)] if len(f1) > 1
                 else _fit_line(f1[0], LCD_COLS, center=center))
            b = (f2[i % len(f2)] if len(f2) > 1
                 else _fit_line(f2[0], LCD_COLS, center=center))
            lcd_show(a, b, center=False)
            time.sleep(step_s)
    time.sleep(0.25)

# =========================================================
# Eye animations (custom LCD characters via CGRAM)
# Each eye is 2 characters wide (10 x 8 pixels).
# CGRAM layout:
#   Slots 0-1: Left eye  (left-half, right-half)
#   Slots 2-3: Right eye (left-half, right-half)
#   Slots 4-6: Mouth     (left, center, right)
#   Slot 7:    Free
# =========================================================
_EYES = {
    #                   left-half                              right-half
    # .XXXX|XXXX.   pupil centered
    "center": ([0x0F, 0x10, 0x10, 0x13, 0x13, 0x10, 0x10, 0x0F],
               [0x1E, 0x01, 0x01, 0x19, 0x19, 0x01, 0x01, 0x1E]),
    # .XXXX|XXXX.   pupil shifted left
    "left":   ([0x0F, 0x10, 0x10, 0x16, 0x16, 0x10, 0x10, 0x0F],
               [0x1E, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x1E]),
    # .XXXX|XXXX.   pupil shifted right
    "right":  ([0x0F, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x0F],
               [0x1E, 0x01, 0x01, 0x0D, 0x0D, 0x01, 0x01, 0x1E]),
    # closed line
    "blink":  ([0x00, 0x00, 0x00, 0x0F, 0x0F, 0x00, 0x00, 0x00],
               [0x00, 0x00, 0x00, 0x1E, 0x1E, 0x00, 0x00, 0x00]),
    # happy crescent (^_^)
    "happy":  ([0x0F, 0x10, 0x10, 0x10, 0x08, 0x07, 0x00, 0x00],
               [0x1E, 0x01, 0x01, 0x01, 0x02, 0x1C, 0x00, 0x00]),
    # wide/filled outline — startled look
    "surprised": ([0x0F, 0x10, 0x14, 0x17, 0x17, 0x14, 0x10, 0x0F],
                  [0x1E, 0x01, 0x05, 0x1D, 0x1D, 0x05, 0x01, 0x1E]),
    # top half blanked (eyelid drooping), only bottom visible
    "sleepy": ([0x00, 0x00, 0x00, 0x00, 0x0F, 0x10, 0x10, 0x0F],
               [0x00, 0x00, 0x00, 0x00, 0x1E, 0x01, 0x01, 0x1E]),
    # cheerful closed arc — used as right eye in wink tuple
    "wink_shut": ([0x00, 0x00, 0x0F, 0x10, 0x08, 0x07, 0x00, 0x00],
                  [0x00, 0x00, 0x1E, 0x01, 0x02, 0x1C, 0x00, 0x00]),
}

# Mouth bitmaps — 3 chars wide (CGRAM slots 4, 5, 6) on its own row
# Each mouth: (left_char, center_char, right_char)
_MOUTHS = {
    #  neutral: flat line across 3 chars
    "neutral": ([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00],   # ..._/
                [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F, 0x00],   # _____
                [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x00]),  # \_...
    #  smile: curved up
    "smile":   ([0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x1C],   #    /--
                [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F],   # _____
                [0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x08, 0x07]),  # --\
    #  open: oval mouth
    "open":    ([0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01],   #   (
                [0x00, 0x00, 0x00, 0x0E, 0x11, 0x00, 0x00, 0x11],   #  O
                [0x00, 0x00, 0x00, 0x00, 0x10, 0x08, 0x08, 0x10]),  #   )
}

# Which mouth pairs with which eye expression
_MOUTH_FOR_EXPR = {
    "center":    "neutral",
    "left":      "neutral",
    "right":     "neutral",
    "blink":     "neutral",
    "happy":     "smile",
    "surprised": "open",
    "sleepy":    "neutral",
    "wink_shut": "smile",
}

_loaded_expr_l = None
_loaded_expr_r = None
_loaded_mouth = None

def _lcd_create_char(slot, bitmap):
    lcd_cmd(0x40 | (slot << 3))
    for b in bitmap:
        lcd_data(b)

def _load_expression(left_name, right_name=None):
    """Write eye bitmaps into CGRAM slots 0-1 (left) and 2-3 (right).
    If right_name is None, both eyes use left_name (symmetric)."""
    global _loaded_expr_l, _loaded_expr_r
    if right_name is None:
        right_name = left_name
    if left_name == _loaded_expr_l and right_name == _loaded_expr_r:
        return
    _loaded_expr_l = left_name
    _loaded_expr_r = right_name
    ll, lr = _EYES[left_name]
    rl, rr = _EYES[right_name]
    _lcd_create_char(0, ll)
    _lcd_create_char(1, lr)
    _lcd_create_char(2, rl)
    _lcd_create_char(3, rr)

def _load_mouth(name):
    """Write the 3-char mouth bitmaps into CGRAM slots 4-6 if changed."""
    global _loaded_mouth
    if name == _loaded_mouth:
        return
    _loaded_mouth = name
    left, center, right = _MOUTHS[name]
    _lcd_create_char(4, left)
    _lcd_create_char(5, center)
    _lcd_create_char(6, right)

# Animation sequence: (expression_name, duration_seconds)
# expression_name can be a string (symmetric) or tuple (left_expr, right_expr)
EYE_SEQUENCE = [
    ("center", 3.0),
    ("blink",  0.15),
    ("center", 2.0),
    ("left",   1.5),
    ("center", 1.0),
    ("right",  1.5),
    ("center", 2.0),
    ("blink",  0.15),
    ("center", 2.0),
    (("center", "wink_shut"), 1.5),
    ("center", 2.0),
    ("happy",  2.0),
]

_eye_idx = 0
_eye_t = 0.0

def _reset_eye_anim():
    global _eye_idx, _eye_t
    _eye_idx = 0
    _eye_t = time.monotonic()

def _current_eye():
    """Advance the animation and return the current expression.
    Returns a string (symmetric) or tuple (left, right) for asymmetric."""
    global _eye_idx, _eye_t
    now = time.monotonic()
    name, dur = EYE_SEQUENCE[_eye_idx]
    if (now - _eye_t) >= dur:
        _eye_idx = (_eye_idx + 1) % len(EYE_SEQUENCE)
        _eye_t = now
        name = EYE_SEQUENCE[_eye_idx][0]
    return name

# Separate change-detection for the eye display
_last_eye_state = None

def lcd_show_eyes(text, expr_name, bat_str, mouth=None):
    """Draw face on rows 0-1, text + battery on row 3.
    expr_name can be a string or tuple (left_expr, right_expr)."""
    global _last_lcd_1, _last_lcd_2, _last_eye_state

    # Unpack asymmetric expression
    if isinstance(expr_name, tuple):
        left_expr, right_expr = expr_name
    else:
        left_expr = expr_name
        right_expr = None

    if mouth is None:
        mouth = _MOUTH_FOR_EXPR.get(left_expr, "neutral")
    if bat_str:
        bottom = _lcd_line(text, bat_str)
    else:
        bottom = _fit_line(text, LCD_COLS)
    state = (left_expr, right_expr, mouth, bat_str, bottom)
    if state == _last_eye_state:
        return
    _last_eye_state = state
    _last_lcd_1 = None
    _last_lcd_2 = None

    _load_expression(left_expr, right_expr)
    _load_mouth(mouth)

    lcd_clear()
    # Row 0: eyes        "      LL   RR      "
    lcd_set_cursor(0, 6)
    lcd_data(0); lcd_data(1)       # left eye  (slots 0,1)
    lcd_set_cursor(0, 11)
    lcd_data(2); lcd_data(3)       # right eye (slots 2,3)
    # Row 1: mouth       "       MMM         "
    lcd_set_cursor(1, 8)
    lcd_data(4); lcd_data(5); lcd_data(6)
    # Row 2: (empty — breathing room)
    # Row 3: text + battery
    lcd_set_cursor(3, 0)
    lcd_print(bottom)

# =========================================================
# Battery monitoring (Pimoroni Pico LiPo)
# ADC on VOLTAGE_MONITOR pin through on-board voltage divider
# =========================================================
_bat_adc = analogio.AnalogIn(board.BAT_SENSE)
_bat_pct_cache = -1
_bat_last_read = 0.0

def read_battery_pct():
    """Read LiPo voltage via ADC, return 0-100 %.  Cached for BAT_UPDATE_S."""
    global _bat_pct_cache, _bat_last_read
    now = time.monotonic()
    if _bat_pct_cache >= 0 and (now - _bat_last_read) < BAT_UPDATE_S:
        return _bat_pct_cache
    # average a few samples for stability
    total = 0
    for _ in range(5):
        total += _bat_adc.value
        time.sleep(0.001)
    voltage = (total / 5 / 65535) * 3.3 * BAT_DIVIDER
    pct = int((voltage - BAT_EMPTY_V) / (BAT_FULL_V - BAT_EMPTY_V) * 100)
    _bat_pct_cache = max(0, min(100, pct))
    _bat_last_read = now
    return _bat_pct_cache

def _bat_str():
    """Right-hand label for the idle screen, e.g. '85%'."""
    return f"{read_battery_pct()}%"

def _check_low_battery():
    """Return 'critical', 'low', or None based on battery level."""
    pct = read_battery_pct()
    if pct <= BAT_CRIT_PCT:
        return "critical"
    if pct <= BAT_LOW_PCT:
        return "low"
    return None

# =========================================================
# Interaction counter (NVM — non-volatile memory)
# Uses microcontroller.nvm[0:4] as a 4-byte little-endian uint32
# =========================================================
_interaction_count = 0
_interaction_pending = 0          # touches since last NVM write

def _load_interaction_count():
    global _interaction_count
    raw = microcontroller.nvm[0:4]
    val = int.from_bytes(raw, "little")
    # Sanitize: uninitialized NVM is often 0xFF…FF
    if val > 1_000_000:
        val = 0
    _interaction_count = val

def _save_interaction_count():
    global _interaction_pending
    _interaction_pending = 0
    microcontroller.nvm[0:4] = _interaction_count.to_bytes(4, "little")

def _increment_interaction():
    global _interaction_count, _interaction_pending
    _interaction_count += 1
    _interaction_pending += 1
    if _interaction_pending >= 10:
        _save_interaction_count()

_MILESTONES = (50, 100, 200, 500, 1000)

def _check_milestone():
    """If the current count is a milestone, show celebration and return True."""
    if _interaction_count in _MILESTONES:
        lcd_show_eyes(f"Poked {_interaction_count} times!", "happy", "")
        time.sleep(3.0)
        return True
    return False

# =========================================================
# Easter egg (3-pad combo)
# =========================================================
_last_pads = []

def _check_easter_egg(pad_idx):
    """Track last 3 pads pressed. If they match EASTER_EGG_SEQ,
    show secret message and return True (skip normal playback)."""
    global _last_pads
    _last_pads.append(pad_idx)
    if len(_last_pads) > len(EASTER_EGG_SEQ):
        _last_pads = _last_pads[-len(EASTER_EGG_SEQ):]
    if _last_pads == EASTER_EGG_SEQ:
        _last_pads = []
        lcd_show_eyes(EASTER_EGG_MSG, "happy", "")
        time.sleep(3.0)
        return True
    return False

# =========================================================
# Idle chatter
# =========================================================
_chatter_idx = -1                 # -1 = show default text first
_chatter_t = 0.0                  # time of last rotation

def _get_chatter_text(default):
    """Return default text for the first cycle, then rotate through
    CLIMATE_MSGS every IDLE_CHATTER_S seconds."""
    global _chatter_idx, _chatter_t
    now = time.monotonic()
    if _chatter_idx < 0:
        # First cycle — use default
        if (now - _chatter_t) >= IDLE_CHATTER_S:
            _chatter_idx = 0
            _chatter_t = now
        return default
    if (now - _chatter_t) >= IDLE_CHATTER_S:
        _chatter_idx = (_chatter_idx + 1) % len(CLIMATE_MSGS)
        _chatter_t = now
    return CLIMATE_MSGS[_chatter_idx]

def _reset_chatter():
    global _chatter_idx, _chatter_t
    _chatter_idx = -1
    _chatter_t = time.monotonic()

# =========================================================
# LCD status helpers
# =========================================================
def lcd_boot():
    lcd_show("Booting...", "please wait", center=True)
    time.sleep(2.0)

def _play_boot_sound():
    """Play Boot.wav if it exists on the SD card. Skip silently if missing."""
    try:
        f = open(BOOT_WAV, "rb")
        f.close()
    except OSError:
        return
    amp_en.value = True
    time.sleep(0.15)
    try:
        with open(BOOT_WAV, "rb") as f:
            wav = audiocore.WaveFile(f)
            audio.play(wav)
            while audio.playing:
                time.sleep(0.01)
    except OSError:
        pass
    audio.stop()
    time.sleep(0.15)
    amp_en.value = False

def lcd_idle_armed(countdown_s=None):
    expr = _current_eye()
    bat = _bat_str()
    # Force sleepy eyes in the last 60 s before sleep
    if countdown_s is not None and countdown_s <= COUNTDOWN_SHOW_S:
        expr = "sleepy"
        m, s = divmod(max(0, int(countdown_s)), 60)
        txt = f"Touch a panel {m}:{s:02d}"
        lcd_show_eyes(txt, expr, bat)
    else:
        txt = _get_chatter_text("Touch a panel")
        lcd_show_eyes(txt, expr, bat)

def lcd_idle_disarmed(countdown_s=None):
    expr = _current_eye()
    bat = _bat_str()
    # Force sleepy eyes in the last 60 s before sleep
    if countdown_s is not None and countdown_s <= COUNTDOWN_SHOW_S:
        expr = "sleepy"
        m, s = divmod(max(0, int(countdown_s)), 60)
        txt = f"Press I/O {m}:{s:02d}"
        lcd_show_eyes(txt, expr, bat)
    else:
        txt = _get_chatter_text("Press I/O")
        lcd_show_eyes(txt, expr, bat)

def lcd_playing(label, expr=None):
    if expr is None:
        expr = _current_eye()
    lcd_show_eyes(label, expr, "", mouth="open")

def lcd_missing(name):
    lcd_show("Missing file", name, center=False)

def lcd_sleeping():
    lcd_show("Sleeping...", "Press I/O", center=True)

def lcd_sleeping_face():
    """Draw closed eyes + mouth on rows 0-1, Zzz on row 2, Press I/O on row 3."""
    global _last_lcd_1, _last_lcd_2, _last_eye_state
    _last_eye_state = None
    _last_lcd_1 = None
    _last_lcd_2 = None

    _load_expression("blink")
    _load_mouth("neutral")

    lcd_clear()
    # Row 0: closed eyes
    lcd_set_cursor(0, 6)
    lcd_data(0); lcd_data(1)
    lcd_set_cursor(0, 11)
    lcd_data(2); lcd_data(3)
    # Row 1: mouth
    lcd_set_cursor(1, 8)
    lcd_data(4); lcd_data(5); lcd_data(6)
    # Row 2: Zzz
    lcd_set_cursor(2, 0)
    lcd_print(_fit_line("Zzz", LCD_COLS, center=True))
    # Row 3: Press I/O
    lcd_set_cursor(3, 0)
    lcd_print(_fit_line("Press I/O", LCD_COLS, center=True))

# =========================================================
# AMP ENABLE (MAX98357A SD -> GP22)
# =========================================================
amp_en = digitalio.DigitalInOut(board.GP22)
amp_en.direction = digitalio.Direction.OUTPUT
amp_en.value = False

# =========================================================
# Mac mini button -> GP21 to GND (active LOW)
# =========================================================
reset_btn = digitalio.DigitalInOut(board.GP21)
reset_btn.direction = digitalio.Direction.INPUT
reset_btn.pull = digitalio.Pull.UP

def start_pressed():
    return not reset_btn.value

# =========================================================
# SD Card (SPI)  SCK=GP2  MOSI=GP3  MISO=GP4  CS=GP5
# =========================================================
spi = busio.SPI(board.GP2, MOSI=board.GP3, MISO=board.GP4)
sd = sdcardio.SDCard(spi, board.GP5)
storage.mount(storage.VfsFat(sd), "/sd")

# =========================================================
# Audio (I2S)  DATA=GP9  BCLK=GP10  LRC=GP11
# =========================================================
audio = audiobusio.I2SOut(
    bit_clock=board.GP10,
    word_select=board.GP11,
    data=board.GP9
)

# =========================================================
# Touch pads
# =========================================================
TOUCH_MAP = [
    (board.GP6,  f"{SOUNDS_DIR}/StartUp.wav", "StartUp"),
    (board.GP7,  f"{SOUNDS_DIR}/Tip2.wav",    "Tip2"),
    (board.GP8,  f"{SOUNDS_DIR}/Tip3.wav",    "Tip3"),
    (board.GP16, f"{SOUNDS_DIR}/Tip4.wav",    "Tip4"),
    (board.GP17, f"{SOUNDS_DIR}/Tip5.wav",    "Tip5"),
]

touch_inputs = []
for pin, _, _ in TOUCH_MAP:
    t = digitalio.DigitalInOut(pin)
    t.direction = digitalio.Direction.INPUT
    t.pull = digitalio.Pull.DOWN
    touch_inputs.append(t)

# Debounce state per pad
touch_high_since = [0.0] * len(touch_inputs)   # monotonic time pin went HIGH
touch_triggered  = [False] * len(touch_inputs)  # already fired this press?

# =========================================================
# State
# =========================================================
last_activity = time.monotonic()
sleeping = False
armed = False
_sleep_start = 0.0
_bat_flash_on = False
_bat_flash_t = 0.0

# =========================================================
# Audio helpers
# =========================================================
def play_wav(path, label, allow_start_interrupt=True, expr=None):
    global last_activity
    last_activity = time.monotonic()

    if audio.playing:
        audio.stop()
        time.sleep(0.05)

    lcd_playing(label, expr=expr)

    amp_en.value = True
    time.sleep(0.15)

    try:
        with open(path, "rb") as f:
            wav = audiocore.WaveFile(f)
            audio.play(wav)
            while audio.playing:
                if allow_start_interrupt and start_pressed():
                    audio.stop()
                    break
                lcd_playing(label, expr=expr)
                time.sleep(0.01)
    except OSError:
        amp_en.value = False
        lcd_missing(path.split("/")[-1])
        time.sleep(1.2)
        return

    audio.stop()
    time.sleep(0.15)
    amp_en.value = False

def play_start_button_sound():
    while start_pressed():
        time.sleep(0.01)
    time.sleep(0.05)
    play_wav(f"{SOUNDS_DIR}/StartButton.wav",
             "Start Button", allow_start_interrupt=False)

# =========================================================
# Sleep / Wake
# =========================================================
def _set_backlight(on):
    """Change backlight state and push the bit to the PCF8574 immediately."""
    global _backlight_on
    want = bool(on)
    if _backlight_on == want:
        return
    _backlight_on = want
    _write_byte(0x00)   # all data/control lines stay low; only BL bit changes

def go_to_sleep():
    global sleeping, armed, last_activity, _sleep_start
    armed = False
    amp_en.value = False
    _save_interaction_count()       # persist count before sleeping
    lcd_sleeping_face()             # closed eyes + Zzz + Press I/O
    time.sleep(SLEEP_MESSAGE_S)
    sleeping = True
    _sleep_start = time.monotonic()
    last_activity = time.monotonic()

def wake_up():
    global sleeping, last_activity, armed
    _set_backlight(True)
    lcd_reset()                     # hard reset prevents garbled characters
    sleeping = False
    last_activity = time.monotonic()
    _reset_chatter()
    lcd_boot()
    _play_boot_sound()
    play_start_button_sound()
    armed = True
    _reset_eye_anim()
    lcd_idle_armed()

# =========================================================
# Power Off (deep sleep — minimal battery drain)
# Hold I/O button for POWER_OFF_HOLD_S to trigger.
# Press I/O again to wake (board restarts from the top).
# =========================================================
def power_off():
    """Shut down peripherals and enter deep sleep."""
    _save_interaction_count()       # persist count before power off
    amp_en.value = False
    if audio.playing:
        audio.stop()
    lcd_show("Powering off...", "", center=True)
    time.sleep(2.0)
    lcd_clear()
    _set_backlight(False)

    # Wait for button release so it doesn't immediately wake
    while start_pressed():
        time.sleep(0.01)
    time.sleep(0.1)

    # Deep sleep until I/O button is pressed again (GP21 goes LOW)
    pin_alarm = alarm.pin.PinAlarm(pin=board.GP21, value=False, pull=True)
    alarm.exit_and_deep_sleep_until_alarms(pin_alarm)

# =========================================================
# MAIN LOOP
# =========================================================
lcd_backlight(True)
lcd_reset()

# Load interaction count from NVM
_load_interaction_count()

lcd_boot()

# Show interaction count on boot
if _interaction_count > 0:
    lcd_show(f"Poked {_interaction_count} times!", "", center=True)
    time.sleep(2.0)

_play_boot_sound()
_reset_eye_anim()
_reset_chatter()
lcd_idle_disarmed()

while True:
    now = time.monotonic()
    remaining = SLEEP_AFTER_S - (now - last_activity)

    # ---- Low battery check ----
    bat_level = _check_low_battery()
    if bat_level == "critical":
        _save_interaction_count()
        power_off()
    elif bat_level == "low" and not sleeping:
        # Flash warning on row 3 without full redraw
        if (now - _bat_flash_t) >= 2.0:
            _bat_flash_on = not _bat_flash_on
            _bat_flash_t = now
            lcd_set_cursor(3, 0)
            if _bat_flash_on:
                lcd_print(_fit_line("!! Low Battery !!", LCD_COLS, center=True))
            else:
                lcd_print(_fit_line("", LCD_COLS))

    # ---- I/O button: wait in place to distinguish short vs long press ----
    if start_pressed():
        press_start = time.monotonic()
        long_pressed = False
        while start_pressed():
            if (time.monotonic() - press_start) >= POWER_OFF_HOLD_S:
                long_pressed = True
                power_off()      # does not return; board resets on wake
            time.sleep(0.01)

        # short press — released before 5 s
        if not long_pressed:
            last_activity = time.monotonic()
            _reset_chatter()
            if sleeping:
                wake_up()
            else:
                play_start_button_sound()
                armed = True
                _reset_eye_anim()
                lcd_idle_armed()
            time.sleep(0.2)
            continue

    # ---- enter sleep after inactivity ----
    if not sleeping and remaining <= 0:
        go_to_sleep()
        continue

    # ---- sleeping: pulse backlight ----
    if sleeping:
        cycle = (now - _sleep_start) % SLEEP_PULSE_S
        bl_on = cycle < (SLEEP_PULSE_S * 0.4)   # 40 % on, 60 % off
        _set_backlight(bl_on)
        time.sleep(0.05)
        continue

    # ---- Touch pads (armed only) ----
    if armed and not audio.playing:
        for i, (_, wav_path, label) in enumerate(TOUCH_MAP):
            current = touch_inputs[i].value

            if current and not touch_triggered[i]:
                # pin is HIGH — start or continue debounce window
                if touch_high_since[i] == 0.0:
                    touch_high_since[i] = now
                elif (now - touch_high_since[i]) >= (TOUCH_DEBOUNCE_MS / 1000):
                    # debounce passed — fire
                    touch_triggered[i] = True
                    last_activity = time.monotonic()
                    _reset_chatter()
                    _increment_interaction()

                    # Easter egg check
                    if _check_easter_egg(i):
                        # skip normal playback
                        pass
                    else:
                        msg = random.choice(CLIMATE_MSGS)
                        pad_expr = TOUCH_EXPR[i] if i < len(TOUCH_EXPR) else None
                        play_wav(wav_path, msg,
                                 allow_start_interrupt=True, expr=pad_expr)

                    # Milestone check
                    _check_milestone()

                    # wait for release
                    if RELEASE_REQUIRED:
                        t0 = time.monotonic()
                        while touch_inputs[i].value:
                            if time.monotonic() - t0 > 5.0:
                                break
                            time.sleep(0.01)

                    # cooldown
                    t1 = time.monotonic()
                    while time.monotonic() - t1 < COOLDOWN_S:
                        time.sleep(0.01)
                    break   # re-scan from the top next iteration

            if not current:
                touch_high_since[i] = 0.0
                touch_triggered[i] = False

        # refresh idle display (battery + countdown)
        now2 = time.monotonic()
        rem2 = SLEEP_AFTER_S - (now2 - last_activity)
        lcd_idle_armed(countdown_s=max(0, rem2))

    elif not armed and not sleeping:
        # keep debounce state clean while disarmed
        for i in range(len(touch_inputs)):
            if not touch_inputs[i].value:
                touch_high_since[i] = 0.0
                touch_triggered[i] = False

        # refresh disarmed display (battery + countdown)
        lcd_idle_disarmed(countdown_s=max(0, remaining))

    time.sleep(0.01)
