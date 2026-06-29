# ============================================================
# audient_evo/device.py
# ============================================================
"""
High-dial EVO 8 device API.
This is the primary interface intended for UI and scripting.
"""

from typing import Sequence, Optional
import logging

from .protocol import EvoProtocol, InBlock, OutBlock, MonBlock
from .transport import EvoUsbTransport
from .util import gain_to_bytes, percent_to_gain_step, mon_value_to_bytes, \
    percent_to_mon_step, bytes_to_gain, gain_step_to_percent, bytes_to_mon_value, \
    bytes_to_volume, ui_volume_to_alsa, alsa_volume_to_ui

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Evo8Device:
    """High-dial user-facing device API."""

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

    def __init__(self, transport: EvoUsbTransport):
        self.transport = transport
        self.device_controlled_by_app = True
        self._last_state: Optional[bytes] = None
        self.last_error: Optional[str] = None

    # --------------------------------------------------------
    # Control ownership
    # --------------------------------------------------------

    def toggle_driver_control(self) -> None:
        if self.device_controlled_by_app:
            self.transport.release()
        else:
            self.transport.connect()
        self.device_controlled_by_app = not self.device_controlled_by_app

    # ---------------- Input Helpers -----------------------

    def _set_input(self, block: InBlock, ch: int, data: bytes) -> bool:
        if not self.device_controlled_by_app:
            return False
        try:
            self.transport.ctrl_set(
                EvoProtocol.ch_addr(block, ch),
                EvoProtocol.IDX_INPUT,
                data,
            )
            return True
        except Exception as e:
            logger.error(f"Error setting Input {block.name} ch {ch}: {e}")
            return False

    def _get_input(self, block: InBlock, ch: int) -> Optional[bytes]:
        if not self.device_controlled_by_app:
            return None
        try:
            data = self.transport.ctrl_get(
                EvoProtocol.ch_addr(block, ch),
                EvoProtocol.IDX_INPUT,
            )
            return data
        except Exception as e:
            logger.error(f"Error getting Input {block.name} ch {ch}: {e}")
            return None

    # ---------------- Input controls ----------------

    def set_gain(self, ch: int, value: int) -> bool:
        gain_byte = gain_to_bytes(percent_to_gain_step(value))
        return self._set_input(InBlock.GAIN, ch, gain_byte)

    def get_gain(self, ch: int) -> int:
        gain_byte = self._get_input(InBlock.GAIN, ch)
        if gain_byte is None: return -1
        try:
            return gain_step_to_percent(bytes_to_gain(gain_byte))
        except KeyError:
            return -1

    def set_phantom(self, ch: int, state: bool) -> bool:
        return self._set_input(InBlock.PHANTOM, ch, b'\x01' if state else b'\x00')

    def get_phantom(self, ch: int) -> bool:
        state_byte = self._get_input(InBlock.PHANTOM, ch)
        return state_byte == b'\x01'

    def set_mic_mute(self, ch: int, state: bool) -> bool:
        return self._set_input(InBlock.MUTE, ch, b'\x01' if state else b'\x00')

    def get_mic_mute(self, ch: int) -> bool:
        state_byte = self._get_input(InBlock.MUTE, ch)
        return state_byte == b'\x01'

    def set_mono(self, ch: int, state: bool) -> bool:
        return self._set_input(InBlock.MONO, ch, b'\x01' if state else b'\x00')

    def get_mono(self, ch: int) -> bool:
        state_byte = self._get_input(InBlock.MONO, ch)
        return state_byte == b'\x01'

    # ---------------- Output controls ----------------

    def set_volume(self, volume: int, out_ch: Sequence[int] = (1, 2)) -> bool:
        if not self.device_controlled_by_app:
            return False
        try:
            #volume_byte = volume_to_bytes(percent_to_out_step(volume))
            volume_byte = b'\x00' + ui_volume_to_alsa(volume).to_bytes(1, "little") + b'\xff\xff'
            for ch in out_ch:
                self.transport.ctrl_set(
                    EvoProtocol.ch_addr(OutBlock.VOLUME, ch),
                    EvoProtocol.IDX_OUTPUT,
                    volume_byte,
                )
            return True
        except Exception as e:
            logger.error(f"Error setting volume: {e}")
            return False

    def get_volume(self, out_ch: Sequence[int] = (1, 2)) -> int:
        if not self.device_controlled_by_app:
            return -1
        try:
            volume = 0
            for ch in out_ch:
                vol_byte = self.transport.ctrl_get(
                    EvoProtocol.ch_addr(OutBlock.VOLUME, ch),
                    EvoProtocol.IDX_OUTPUT,
                    1
                )
                if vol_byte:
                    volume = alsa_volume_to_ui(bytes_to_volume(vol_byte))
            return volume
        except Exception as e:
            logger.error(f"Error getting volume: {e}")
            return -1

    def set_out_mute(self, state: bool, out_ch: Sequence[int] = (1, 2)) -> bool:
        if not self.device_controlled_by_app:
            return False
        try:
            state_byte = b'\x01' if state else b'\x00'
            for ch in out_ch:
                self.transport.ctrl_set(
                    EvoProtocol.ch_addr(OutBlock.MUTE, ch),
                    EvoProtocol.IDX_OUTPUT,
                    state_byte
                )
            return True
        except Exception as e:
            logger.error(f"Error setting out mute: {e}")
            return False

    def get_out_mute(self, out_ch: Sequence[int] = (1, 2)) -> bool:
        if not self.device_controlled_by_app:
            return False
        try:
            for ch in out_ch:
                state_byte = self.transport.ctrl_get(
                    EvoProtocol.ch_addr(OutBlock.MUTE, ch),
                    EvoProtocol.IDX_OUTPUT,
                    1
                )
                if state_byte == b'\x01':
                    state = True
                else: state = False
            return state
        except Exception as e:
            logger.error(f"Error getting out mute: {e}")
            return False

    # ---------------- Monitor mixer ----------------

    def set_monitor(self, value: int, in_ch: int, out_ch: Sequence[int] = (1, 2)) -> bool:
        if not self.device_controlled_by_app:
            return False
        try:
            monitor_byte = mon_value_to_bytes(percent_to_mon_step(value))
            for ch in out_ch:
                self.transport.ctrl_set(
                    EvoProtocol.mon_addr(MonBlock.VOLUME, in_ch, ch),
                    EvoProtocol.IDX_MONITOR,
                    monitor_byte,
                )
            return True
        except Exception as e:
            logger.error(f"Error setting monitor: {e}")
            return False

    def get_monitor(self, in_ch: int, out_ch: Sequence[int] = (1, 2)) -> int:
        if not self.device_controlled_by_app:
            return -1
        try:
            monitor_vol= bytes_to_mon_value(self.transport.ctrl_get(
                    EvoProtocol.mon_addr(MonBlock.VOLUME, in_ch, out_ch[0]),
                    EvoProtocol.IDX_MONITOR,
                ))
            return monitor_vol
        except Exception as e:
            logger.error(f"Error getting monitor: {e}")
            return -1

    # ---------------- Loopback ----------------

    def get_loopback(self) -> Optional[bytes]:  # TODO: refactor to return string instead of bytes
        if not self.device_controlled_by_app:
            return None
        try:
            return self.transport.ctrl_get(0x0604, 0x3300, length=1)
        except Exception as e:
            logger.error(f"Error getting loopback: {e}")
            return None

    def set_loopback(self, loopback_group: str) -> bool:

        if loopback_group not in self.LOOPBACK_MAPPINGS:
            raise ValueError(f"Invalid loopback group. Supported: {list(self.LOOPBACK_MAPPINGS.keys())}")

        try:
            self.transport.ctrl_set(0x0604, 0x3300, self.LOOPBACK_MAPPINGS[loopback_group][0])
            self.transport.ctrl_set(0x0605, 0x3300, self.LOOPBACK_MAPPINGS[loopback_group][1])
            logger.info(f"Set loopback to {loopback_group}")
            return True
        except Exception as e:
            logger.error(f"Failed to set loopback to {loopback_group}: {e}")
            return False

    # ---------------- Sample Rate ----------------

    def get_sample_rate(self) -> int:
        sr_bytes = self.transport.ctrl_get(0x2900,0x0200)
        return self.SAMPLE_RATE_INV.get(sr_bytes, -1)

    def set_sample_rate(self, sr:int) -> bool:

        if sr not in self.SAMPLE_RATES:
            raise ValueError(f"Unsupported sample rate {sr}. Supported: {list(self.SAMPLE_RATES.keys())}")

        self.transport.ctrl_set(0x2900, 0x0200, self.SAMPLE_RATES[sr])
        return True

    # ---------------- Events ----------------

    def event_listen(self) -> Optional[bytes]:
        if self.device_controlled_by_app:
            return self.transport.ctrl_get(EvoProtocol.IDX_BUFFER, 0x0200)
        return None

    def event_changed(self, new_state: bytes) -> bool:
        if new_state != self._last_state:
            self._last_state = new_state
            return True
        return False