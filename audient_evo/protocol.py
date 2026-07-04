# ============================================================
# audient_evo/protocol.py
# ============================================================
"""
EVO USB protocol helpers
------------------------
Pure address-calculation logic for the Audient EVO 8 firmware.
No USB or device state is handled here.
"""

from enum import IntEnum

class InBlock(IntEnum):
    """Input channel control blocks (wValue offsets)."""
    PHANTOM = 0x0000
    GAIN    = 0x0100
    MUTE    = 0x0200
    MONO    = 0x0300

class OutBlock(IntEnum):
    """Output channel control blocks (wValue offsets)."""
    VOLUME  = 0x0000
    MUTE    = 0x0100

class MonBlock(IntEnum):
    """Monitor mixer control blocks (wValue offsets)."""
    VOLUME  = 0x0100

class EvoProtocol:
    """Static helpers for EVO firmware addressing. (wIndex)"""

    IDX_INPUT   = 0x3A00    # Extension Unit 58
    IDX_OUTPUT  = 0x3B00    # Extension Unit 59
    IDX_MONITOR = 0x3C00    # Mixer Unit ID  60 source ID 50
    IDX_BUFFER  = 0x3E00    # Extension Unit 62

    @staticmethod
    def ch_addr(field: int, ch: int) -> int:
        """Return channel-based address (1-based) (final wValue)."""
        return field + (ch - 1)

    @staticmethod
    def mon_addr(field: int, in_ch: int, out_ch: int) -> int:
        """Return monitor matrix address. (final wValue)"""
        return field + (in_ch - 1) * 4 + (out_ch - 1)

class EVO8Addresses:
    SAMPLE_RATE    = (0x2900, 0x0200)
    LOOPBACK_LEFT  = (0x0604, 0x3300)
    LOOPBACK_RIGHT = (0x0605, 0x3300)

    PHANTOM_POWER  = (0x0000, 0x3A00)
    GAIN           = (0x0100, 0x3A00)
    MIC_MUTE       = (0x0200, 0x3A00)
    MIC_MONO       = (0x0300, 0x3A00)

    VOLUME         = (0x0000, 0x3B00)
    OUTPUT_MUTE    = (0x0100, 0x3B00)

    MONITOR        = (0x0100, 0x3C00)
    #MONITOR_MUTE
    #MONITOR_MONO  = (0x0300, 0x3C00)

    def __init__(self, values):
        # Store the tuple internally
        self._values = values

    def __call__(self, index):
        # Allow the instance to be called like a function: instance(0)
        return self._values[index]

    def __repr__(self):
        # Optional: Makes debugging/printing cleaner
        return f"AddressPair{self._values}"

EVO8TOHARDWARE = {
    "sample_rate":      (0x2900, 0x0200),
    "loopback_left":    (0x0604, 0x3300),
    "loopback_right":   (0x0605, 0x3300),

    "phantom_power":    (0x0000, 0x3A00),
    "gain":             (0x0100, 0x3A00),
    "mic_mute":         (0x0200, 0x3A00),
    "mic_mono":         (0x0300, 0x3A00),

    "volume":           (0x0000, 0x3B00),
    "output_mute":      (0x0100, 0x3B00),

    }

SAMPLE_RATES = {
    44100: b'\x44\xAC\x00\x00',
    48000: b'\x80\xBB\x00\x00',
    88200: b'\x88\x58\x01\x00',
    96000: b'\x00\x77\x01\x00'
}

SAMPLE_RATE_INV = {v: k for k, v in SAMPLE_RATES.items()}

LOOPBACK_MAPPINGS = {
    "PC1+2": (b'\x06', b'\x07'),
    "PC3+4": (b'\x08', b'\x09'),
    "LB1+2": (b'\x0a', b'\x0b'),
    "MM1+2": (b'\x0c', b'\x0d'),
    "AM1+2": (b'\x0e', b'\x0f')
}

LOOPBACK_MAPPINGS_INV = {v: k for k, v in LOOPBACK_MAPPINGS.items()}

HARDWARE_TO_CATEGORY = {
    # Unit 58 (Inputs)
    (58, 0x00): "phantom",
    (58, 0x01): "gain",
    (58, 0x02): "mic_mute",
    (58, 0x03): "mono",

    # Unit 59 (Outputs)
    (59, 0x00): "volume",
    (59, 0x01): "out_mute",

    # Unit 60 (Monitor)
    (60, 0x01): "monitor",

    # Unit 2 (sample rate)
    (2, 0x29): "sample_rate",

    # Unit 51 (loopback)
    (51, 0x0604): "loopback_left",
    (51, 0x0605): "loopback_right"
}