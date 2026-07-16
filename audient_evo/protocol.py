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

class EvoUnits:
    """Static helpers for EVO firmware addressing. (wIndex)"""

    IDX_INPUT   = 0x3A00    # Extension Unit 58
    IDX_OUTPUT  = 0x3B00    # Extension Unit 59
    IDX_MONITOR = 0x3C00    # Mixer Unit ID  60 source ID 50
    IDX_BUFFER  = 0x3E00    # Extension Unit 62

SAMPLE_RATES = {
    44100: b'\x44\xAC\x00\x00',
    48000: b'\x80\xBB\x00\x00',
    88200: b'\x88\x58\x01\x00',
    96000: b'\x00\x77\x01\x00'
}

SAMPLE_RATE_INV = {v: k for k, v in SAMPLE_RATES.items()}

LOOPBACK_SOURCES = {
    "PC1+2": (b'\x06', b'\x07'),
    "PC3+4": (b'\x08', b'\x09'),
    "LB1+2": (b'\x0a', b'\x0b'),
    "MM1+2": (b'\x0c', b'\x0d'),
    "AM1+2": (b'\x0e', b'\x0f')
}

LOOPBACK_TARGETS = {
    "PC1+2": (0x0600, 0x0601),
    "PC3+4": (0x0602, 0x0603),
    "LB1+2": (0x0604, 0x0605)
}

LOOPBACK_MAPPINGS_INV = {v: k for k, v in LOOPBACK_SOURCES.items()}

CATEGORY_TO_HARDWARE = {
    "phantom":          {"wValue_base": 0x0000, "wIndex": 0x3A00, "length": 1},
    "gain":             {"wValue_base": 0x0100, "wIndex": 0x3A00, "length": 4},
    "mic_mute":         {"wValue_base": 0x0200, "wIndex": 0x3A00, "length": 1},
    "mic_mono":         {"wValue_base": 0x0300, "wIndex": 0x3A00, "length": 1},
    
    "volume":           {"wValue_base": 0x0000, "wIndex": 0x3B00, "length": 4},
    "out_mute":         {"wValue_base": 0x0100, "wIndex": 0x3B00, "length": 1},
    "out_stereo":       {"wValue_base": 0x0200, "wIndex": 0x3B00, "length": 1},

    "monitor":          {"wValue_base": 0x0100, "wIndex": 0x3C00, "length": 4},

    "monitor_bridge": {"wValue_base": 0x0100, "wIndex": 0x3200, "length": 1}, # EXPERIMENTAL: WILL CRASH

    "sample_rate":      {"wValue_base": 0x0100, "wIndex": 0x2900, "length": 4},
    #"loopback_target":  {"wValue_base": 0x0600, "wIndex": 0x3300, "length": 1},

    #"artist_mix":       {"wValue_base": 0x????, "wIndex": 0x????, "length": 1}, # TODO: CHECK USB DUMPS FOR CORRECT ADDRESSES

    "get_event":       {"wValue_base": 0x0600, "wIndex": 0x3E00, "length": 4},


    }

HARDWARE_TO_CATEGORY = {
    # Unit 58 (Inputs)
    (58, 0x00): "phantom",
    (58, 0x01): "gain",
    (58, 0x02): "mic_mute",
    (58, 0x03): "mic_mono",

    # Unit 59 (Outputs)
    (59, 0x00): "volume",
    (59, 0x01): "out_mute",

    # Unit 60 (Monitor)
    (60, 0x01): "monitor",

    # Unit 2 (sample rate)
    (41, 0x01): "sample_rate",

    # Unit 51 (loopback)
    (51, 0x0604): "loopback_left", # this is the left loopback (LB 1) Target channel (look at LOOPBACK_TARGETS above)
    (51, 0x0605): "loopback_right" # this is the right loopback (LB 2) Target channel
}