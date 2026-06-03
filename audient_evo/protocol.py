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
    IDX_FUI     = 0x0B00    # Feature Unit 11 - Input
    IDX_FUO     = 0x0A00    # Feature Unit 10 - Output
    IDX_EULB    = 0x3200    # Extension Unit 50 - Loopback something
    IDX_EULB2   = 0x3300    # Extension Unit 51 - Loopback mapping

    @staticmethod
    def ch_addr(field: int, ch: int) -> int:
        """Return channel-based address (1-based) (final wValue)."""
        return field + (ch - 1)

    @staticmethod
    def mon_addr(field: int, in_ch: int, out_ch: int) -> int:
        """Return monitor matrix address. (final wValue)"""
        return field + (in_ch - 1) * 4 + (out_ch - 1)