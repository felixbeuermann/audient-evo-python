import struct

# ============================================================================
# USB exceptions
# ============================================================================

class UsbPipeError(RuntimeError):
    pass

class UsbTimeoutError(RuntimeError):
    pass

class UsbProtocolError(RuntimeError):
    pass

class UsbNotBoundError(RuntimeError):
    """USB device not bound or already released."""
    def __init__(self, message: str = "USB device not bound or already released"):
        super().__init__(message)

class DeviceDisconnectedError(RuntimeError):
    def __init__(self, message: str = "USB device disconnected"):
        super().__init__(message)

# ============================================================================
# USB channel / address helpers
# ============================================================================

def calculate_wValue(field: int, ch: int) -> int:
    """Return channel-based address (1-based) (final wValue)."""
    return field + (ch - 1)

def calculate_monitor_wValue(field: int, in_ch: int, out_ch: int) -> int:
    """Return monitor matrix address. (final wValue)"""
    return field + (in_ch - 1) * 4 + (out_ch - 1)

def get_partner_channel(ch: int) -> int:
    """Get Stereo-Partner of channel(1<->2, 3<->4, etc.)."""
    return ch + 1 if ch % 2 != 0 else ch - 1

def split_monitor_channel(mon_ch: int) -> tuple[int, int]:
    """
    Split a combined monitor channel into its input and output channels.
    equation: wValue = base_offset + (in_ch - 1) * 4 + (out_ch - 1)
    """

    # 2. Determine out_ch (remainder of division by 4)
    # Since (out_ch - 1) ranges from 0 to 3, this is modulo 4
    out_ch = (mon_ch % 4) + 1

    # 3. Determine in_ch (integer division by 4)
    in_ch = (mon_ch // 4) + 1

    return in_ch, out_ch

# ============================================================================
# dB ranges
# ============================================================================

MIN_GAIN_DB: int = -2048
MAX_GAIN_DB: int = 12800

MIN_VOL_DB: float = -128.00
MAX_VOL_DB: float = 0.00

MIN_MON_DB: float = -128.00
MAX_MON_DB: float = 8.00

# ============================================================================
# Gain conversion
# ============================================================================

def gain_db_to_percent(gain_db: int) -> int:
    # Clamping into valid range
    clamped = max(MIN_GAIN_DB, min(MAX_GAIN_DB, gain_db))
    # Percent calculation: (raw - min) / (max - min) * 100
    percent = (clamped - MIN_GAIN_DB) / (MAX_GAIN_DB - MIN_GAIN_DB) * 100
    return int(round(percent))

def percent_to_gain_db(percent: int) -> int:
    # Clamp percent into valid 0..100 range
    clamped_percent = max(0.0, min(100.0, float(percent)))
    # Inverse linear calculation: raw = min + (percent / 100) * (max - min)
    gain_db = MIN_GAIN_DB + (clamped_percent / 100.0) * (MAX_GAIN_DB - MIN_GAIN_DB)
    gain_db_round = int(round(gain_db))
    # Pack as 16-bit Signed Integer (Little-Endian)
    return gain_db_round

def gain_bytes_to_db(data: bytes) -> int:
    """Convert raw DSP-Gain-Value to db."""
    if not data or len(data) < 2:
        return -1
    # Unpack 16-bit Signed Integer (Little-Endian)
    return struct.unpack('<h', data[:2])[0]

def db_to_gain_bytes(db: int) -> bytes:
    """Convert db to raw DSP-Gain-Value."""
    return struct.pack('<h', db)

def gain_bytes_to_percent(data: bytes) -> int:
    """Convert raw DSP-Gain-Value to 0-100%."""
    gain_db = gain_bytes_to_db(data)
    return gain_db_to_percent(gain_db)

def percent_to_gain_bytes(percent: int) -> bytes:
    """Convert 0-100% back to raw DSP-Gain-Bytes."""
    gain_db = percent_to_gain_db(percent)
    # Pack as 16-bit Signed Integer (Little-Endian)
    return struct.pack('<h', gain_db)

# ============================================================================
# Volume conversion
# ============================================================================

def generate_out_bytes():               # TODO: maybe replace with alsa mapping ( 0 - 255/4)
    # total number of steps = count of discrete byte1 values
    steps = []
    #print(steps)    # currently 160

    # Helper to append a full 4-byte value
    def add(b0, b1):
        steps.append([b0, b1, 0xFF, 0xFF])

    #add(0x00, 0x00) # added: "Unknown volume byte sequence: b'00 00 FF FF'"
    add(0x00, 0x80)
    add(0x00, 0x81)
    for b1 in range(0x84, 0xe1, 0x01):
        add(0x00, b1)
    for b1 in range(0xe0, 0xff, 0x01): # added: 'Unknown volume byte sequence: 80 E0 FF FF'
        for b0 in (0x00, 0x80):
            add(b0, b1)
    steps.append([0x00, 0xff, 0xff, 0xff])
    steps.append([0x80, 0xff, 0xff, 0xff])
    steps.append([0x00, 0x00, 0x00, 0x00]) # 00 00 ff ff = -128.00 dB, 00 00 00 00 = 0.00 dB
    return steps

_OUTPUT_STEPS = generate_out_bytes()
_OUTPUT_INDEX = {tuple(step): i for i, step in enumerate(_OUTPUT_STEPS)}

def get_vol_list():
    return _OUTPUT_STEPS

def vol_step_to_bytes(value: int) -> bytes:
    return _OUTPUT_STEPS[value]

def bytes_to_vol_step(data: bytes) -> int:
    key = tuple(data)
    try:
        return _OUTPUT_INDEX[key]
    except KeyError:
        key_str = ' '.join(f'{b:02X}' for b in key)
        raise KeyError(f"Unknown volume byte sequence: {key_str}")

def out_step_to_percent(step: int) -> int:
    if not 0 <= step <= 160:
        raise ValueError("step must be in range 0..160")

    return round(step * 100 / 160)

def percent_to_out_step(percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in range 0..100")

    return round(percent * 160 / 100)

def percent_to_vol_db(percent: int) -> float:
    out_step = percent_to_out_step(percent)
    vol_bytes = vol_step_to_bytes(out_step)
    vol_db = decode_uac_volume(vol_bytes)
    return float(f"{vol_db:.2f}")

def vol_db_to_percent(vol_db: float) -> int:
    vol_bytes = encode_uac_volume(vol_db)
    out_step = bytes_to_vol_step(vol_bytes)
    return out_step_to_percent(out_step)

# ============================================================================
# Monitor conversion
# ============================================================================

def generate_mon_bytes():
    steps = []

    # Helper to append a full 4-byte value
    def add(b0, b1):
        steps.append([b0, b1, 0xFF, 0xFF])

    # 1) Coarse region (-128 dB bis -48 dB)
    for b1 in range(0x80, 0xD0, 0x06):
        add(0x00, b1)

    # 2) Half-step region (-48 dB bis -28 dB)
    for b1 in range(0xD0, 0xE4):
        add(0x00, b1)
        add(0x80, b1)

    # 3) Quarter-step region (-28 dB bis -13 dB)
    for b1 in range(0xE4, 0xF3):
        for b0 in (0x00, 0x40, 0x80, 0xC0):
            add(b0, b1)

    # 4) Fine region (5 substeps) NEGATIVE (-13 dB bis -0.2 dB)
    fine = (0x00, 0x34, 0x67, 0x9A, 0xCD)
    for b1 in range(0xF3, 0x100):
        for b0 in fine:
            add(b0, b1)

    # 5) NEW: Fine region POSITIVE (0.0 dB bis +7.8 dB)
    for b1 in range(0x00, 0x08):
        for b0 in fine:
            add(b0, b1)

    add(0x00, 0x08)
    #print(len(steps))

    return steps

_MONITOR_STEPS = generate_mon_bytes()
_MONITOR_INDEX = {tuple(step): i for i, step in enumerate(_MONITOR_STEPS)}

def get_mon_list():
    return _MONITOR_STEPS

def mon_step_to_bytes(value: int) -> bytes:
    return _MONITOR_STEPS[value]

def bytes_to_mon_step(data: bytes) -> int:
    if data == b'\x00\x80\x00\x00':
        return 0  # when the later two bytes are zero then the monitor is off, when it's connected they are both 256
    key = tuple(data)
    try:
        return _MONITOR_INDEX[key]
    except KeyError:
        key_str = ' '.join(f'{b:02X}' for b in key)
        raise KeyError(f"Unknown volume byte sequence: {key_str}")

def mon_step_to_percent(step: int) -> int:
    if not 0 <= step <= 219:
        raise ValueError("step must be in range 0..220")

    return round(step * 100 / 219)

def percent_to_mon_step(percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in range 0..100")

    return round(percent * 219 / 100)

def percent_to_mon_db(percent: int) -> float:
    mon_step = percent_to_mon_step(percent)
    mon_bytes = mon_step_to_bytes(mon_step)
    mon_db = decode_uac_volume(mon_bytes)
    return float(f"{mon_db:.2f}")

def mon_db_to_percent(mon_db: float) -> int:
    mon_bytes = encode_uac_volume(mon_db)
    mon_step = bytes_to_mon_step(mon_bytes)
    return mon_step_to_percent(mon_step)

# ============================================================================
# Step table helpers
# ============================================================================

def remove_duplicate_steps(steps: list) -> list:
    """
    Remove duplicate 4-byte sequences from the steps list while preserving order.
    """
    seen = set()
    unique_steps = []
    for step in steps:
        key = tuple(step)  # convert to tuple so it is hashable
        if key not in seen:
            unique_steps.append(step)
            seen.add(key)
    return unique_steps

def debug_print_step(step):
    print(f"[{step[0]:02X}, {step[1]:02X}, {step[2]:02X}, {step[3]:02X}]")

# ============================================================================
# Generic helpers
# ============================================================================

def fmt_bytes(data: bytes) -> str:
    """Format raw bytes for debug logging."""
    return "[" + " ".join(f"0x{b:02X}" for b in data) + "]"

def is_in_percent_range(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100

def bytes_to_bool(data: bytes) -> bool:
    if len(data) != 1:
        raise ValueError(f"Expected 1 byte, got {len(data)}")

    return data[0] != 0

def bool_to_bytes(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"

# ============================================================================
# UI / ALSA volume mapping
# ============================================================================

UI_MIN = 128
UI_MAX = 255 #255
ALSA_MAX = 254
SHAPE = 2.2

def ui_to_norm(ui: int) -> float:
    if ui <= UI_MIN:
        return 0.0
    if ui >= UI_MAX:
        return 1.0
    return (ui - UI_MIN) / (UI_MAX - UI_MIN)

def norm_to_ui(x: float) -> int:
    return round(UI_MIN + x * (UI_MAX - UI_MIN))

def evo_curve(x: float) -> float:
    p = SHAPE
    return (x ** p) / (x ** p + (1 - x) ** p)

def evo_curve_inv(y: float) -> float:
    p = SHAPE
    return (y ** (1 / p)) / ((y ** (1 / p)) + ((1 - y) ** (1 / p)))

def ui_volume_to_alsa(ui: int) -> int:  # turns Values between 0 - 100 into 128 - 254
    x = ui_to_norm(ui)
    shaped = evo_curve(x)
    return round(shaped * ALSA_MAX)

def alsa_volume_to_ui(alsa: int) -> int: # turns Values between 128 - 254 into 0 - 100
    y = alsa / ALSA_MAX
    x = evo_curve_inv(y)
    return norm_to_ui(x)

# ============================================================================
# UAC volume encoding / decoding
# ============================================================================

def encode_uac_volume(db_val: float) -> bytes:
    """Converts real dB (Decibels) to the 4-byte USB format."""
    if db_val <= -128.0:
        return b'\x00\x80\xff\xff'  # -32768 (Mute)

    raw_val = int(round(db_val * 256))
    raw_val = max(-32768, min(32767, raw_val))

    if db_val == 0.0:
        return struct.pack('<h', raw_val) + b'\x00\x00'
    return struct.pack('<h', raw_val) + b'\xff\xff'

def decode_uac_volume(data: bytes) -> float:
    """Converts 4 raw USB bytes to real dB."""
    if not data or len(data) < 2:
        return -128.0

    # Cleanly handles the special bypass state during boot
    if data == b'\x00\x00\xff\xff':
        return -128.0

    raw_val = struct.unpack('<h', data[:2])[0]
    if raw_val <= -32768:
        return -128.0

    return raw_val / 256.0