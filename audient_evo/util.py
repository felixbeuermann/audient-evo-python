
def gain_step_to_percent(step: int) -> int:
    if not 0 <= step <= 117:
        raise ValueError("step must be in range 0..117")

    return round(step * 100 / 117)

def percent_to_gain_step(percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in range 0..100")

    return round(percent * 117 / 100)

def out_step_to_percent(step: int) -> int:
    if not 0 <= step <= 158:
        raise ValueError("step must be in range 0..120")

    return round(step * 100 / 158)

def percent_to_out_step(percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in range 0..100")

    return round(percent * 158 / 100)

def percent_to_out(percent:int):
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in range 0..100")

    return round(percent * 127 / 100)

def mon_step_to_percent(step: int) -> int:
    if not 0 <= step <= 178:
        raise ValueError("step must be in range 0..178")

    return round(step * 100 / 178)

def percent_to_mon_step(percent: int) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in range 0..100")

    return round(percent * 178 / 100)

def generate_mon_bytes():
    steps = []

    # Helper to append a full 4-byte value
    def add(b0, b1):
        steps.append([b0, b1, 0xFF, 0xFF])

    # 1) Coarse region
    for b1 in range(0x80, 0xD0, 0x06):
        add(0x00, b1)

    # 2) Half-step region
    for b1 in range(0xD0, 0xE4):
        add(0x00, b1)
        add(0x80, b1)

    # 3) Quarter-step region
    for b1 in range(0xE4, 0xF3):
        for b0 in (0x00, 0x40, 0x80, 0xC0):
            add(b0, b1)

    # 4) Fine region (5 substeps)
    fine = (0x00, 0x34, 0x67, 0x9A, 0xCD)
    for b1 in range(0xF3, 0x100):
        for b0 in fine:
            add(b0, b1)

    return steps

_MONITOR_STEPS = generate_mon_bytes()

def debug_print_step(step):
    print(f"[{step[0]:02X}, {step[1]:02X}, {step[2]:02X}, {step[3]:02X}]")

def generate_out_bytes():               # TODO: maybe replace with alsa mapping ( 0 - 255/4)
    # total number of steps = count of discrete byte1 values
    steps = []
    #print(steps)    # currently 158

    # Helper to append a full 4-byte value
    def add(b0, b1):
        steps.append([b0, b1, 0xFF, 0xFF])

    add(0x00, 0x80)
    add(0x00, 0x81)
    for b1 in range(0x84, 0xe1, 0x01):
        add(0x00, b1)
    for b1 in range(0xe1, 0xff, 0x01):
        for b0 in (0x00, 0x80):
            add(b0, b1)
    steps.append([0x00, 0xff, 0xff, 0xff])
    steps.append([0x80, 0xff, 0xff, 0xff])
    steps.append([0x00, 0x00, 0x00, 0x00])
    print(len(steps))
    return steps

_OUTPUT_STEPS = generate_out_bytes()

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

def generate_gain_bytes():
    steps = []
    for gain_step in range(118):  # 0..117 inclusive
        """
        Map a dial/slider integer to the 4-byte USB gain value.
        gain_step: 0 = min, max_step = maximum 118 steps
        Returns: 4-byte value as bytes
        """
        # Determine if we are in high range (248-255) or low range (0-50)
        if gain_step < 16:  # first 16 steps correspond to 248-255
            base = 248
            third_fourth = 0xffff
        else:
            base = 0
            gain_step -= 16  # offset for low range
            third_fourth = 0x0000

        # second_byte = base + integer division by 2
        second_byte = base + (gain_step // 2)

        # first_byte = 0x00 for even steps, 0x80 for odd steps
        first_byte = 0x80 if gain_step % 2 else 0x00

        steps.append(bytes([first_byte, second_byte & 0xFF, (third_fourth >> 8) & 0xFF, third_fourth & 0xFF]))
    return steps

_GAIN_STEPS = generate_gain_bytes()

def _test_volume_roundtrip():
    for i, step in enumerate(_OUTPUT_STEPS):
        decoded = bytes_to_volume(step)
        assert decoded == i, (
            f"Volume mismatch: step {i} → {decoded}, bytes={step}"
        )

def _test_monitor_roundtrip():
    for i, step in enumerate(_MONITOR_STEPS):
        decoded = bytes_to_mon_value(step)
        assert decoded == i, (
            f"Monitor mismatch: step {i} → {decoded}, bytes={step}"
        )

def _test_gain_roundtrip():
    for i, step in enumerate(_GAIN_STEPS):
        decoded = bytes_to_gain(step)
        assert decoded == i, (
            f"Monitor mismatch: step {i} → {decoded}, bytes={step}"
        )

def run_all_codec_tests():
    _test_volume_roundtrip()
    _test_monitor_roundtrip()
    _test_gain_roundtrip()
    print("✅ All EVO8 codec round-trip tests passed")

def volume_to_bytes(value: int) -> bytes:
    return _OUTPUT_STEPS[value]

def gain_to_bytes(value: int) -> bytes:
    return _GAIN_STEPS[value]

def mon_value_to_bytes(value: int) -> bytes:
    return _MONITOR_STEPS[value]

def get_mon_list():
    return _MONITOR_STEPS

def get_gain_list():
    return _GAIN_STEPS

def get_vol_list():
    return _OUTPUT_STEPS

_MONITOR_INDEX = {tuple(step): i for i, step in enumerate(_MONITOR_STEPS)}
_OUTPUT_INDEX = {tuple(step): i for i, step in enumerate(_OUTPUT_STEPS)}
_GAIN_INDEX = {tuple(step): i for i, step in enumerate(_GAIN_STEPS)}

def bytes_to_gain(data: bytes) -> int:
    key = tuple(data)
    if key not in _GAIN_INDEX:
        raise KeyError(f"Unknown volume byte sequence: {key:04X}")
    return _GAIN_INDEX[key]

def bytes_to_volume(data: bytes) -> int:
    key = tuple(data)
    try:
        return _OUTPUT_INDEX[key]
    except KeyError:
        key_str = ' '.join(f'{b:02X}' for b in key)
        raise KeyError(f"Unknown volume byte sequence: {key_str}")

def bytes_to_mon_value(data: bytes) -> int:
    key = tuple(data)
    if key not in _MONITOR_INDEX:
        raise KeyError(f"Unknown volume byte sequence: {key:04X}")
    return _MONITOR_INDEX[key]

def fmt_bytes(data: bytes) -> str:
    """Format raw bytes for debug logging."""
    return "[" + " ".join(f"0x{b:02X}" for b in data) + "]"