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
from src.audient_evo.util import gain_to_bytes, percent_to_gain_step, mon_value_to_bytes, \
    percent_to_mon_step, bytes_to_gain, gain_step_to_percent, bytes_to_mon_value, bytes_to_volume
from src.alsaUiAdapter import ui_volume_to_alsa, alsa_volume_to_ui


class Evo8Device:
    """High-dial user-facing device API."""

    def __init__(self, transport: EvoUsbTransport):
        self.transport = transport
        self.device_controlled_by_app = True
        self._last_state: Optional[bytes] = None
        self.last_error: Optional[str] = None

        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

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
            self.logger.error(f"Error setting Input {block.name} ch {ch}: {e}")
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
            self.logger.error(f"Error getting Input {block.name} ch {ch}: {e}")
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
            self.logger.error(f"Error setting volume: {e}")
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
            self.logger.error(f"Error getting volume: {e}")
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
            self.logger.error(f"Error setting out mute: {e}")
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
            self.logger.error(f"Error getting out mute: {e}")
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
            self.logger.error(f"Error setting monitor: {e}")
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
            self.logger.error(f"Error getting monitor: {e}")
            return -1

    # ---------------- Loopback ----------------

    def get_loopback(self) -> Optional[bytes]:
        if not self.device_controlled_by_app:
            return None
        try:
            return self.transport.ctrl_get(0x0604, 0x3300, length=1)
        except Exception as e:
            self.logger.error(f"Error getting loopback: {e}")
            return None

    def set_loopback(self, ch: int) -> bool:
        success = False
        try:
            if ch == 1 or ch == 2:
                self.transport.ctrl_set( 0x0604, 0x3300, b'\x06')# PC1
                self.transport.ctrl_set( 0x0605, 0x3300, b'\x07')# PC2
                print("set loopback to PC1+2")
                success = True
            elif ch == 3 or ch == 4:
                self.transport.ctrl_set(0x0604, 0x3300, b'\x08')# PC3
                self.transport.ctrl_set(0x0605, 0x3300, b'\x09')# PC4
                print("set loopback to PC3+4")
                success = True
            elif ch == 5 or ch == 6:
                self.transport.ctrl_set(0x0604, 0x3300, b'\x0a')# LB1
                self.transport.ctrl_set(0x0605, 0x3300, b'\x0b')# LB2
                print("set loopback to LB1+2")
                success = True
            elif ch == 7 or ch == 8:
                self.transport.ctrl_set(0x0604, 0x3300, b'\x0c')# MM1
                self.transport.ctrl_set(0x0605, 0x3300, b'\x0d')# MM2
                print("set loopback to Master Mix")
                success = True
            elif ch == 9 or ch == 10:
                self.transport.ctrl_set(0x0604, 0x3300, b'\x0e')# AM1
                self.transport.ctrl_set(0x0605, 0x3300, b'\x0f')# AM2
                print("set loopback to Artist Mix")
                success = True
            else:
                print(f"Unknown loopback group index: {ch}")
        except Exception as e:
            self.logger.error(f"Error setting loopback: {e}")
            return  False
        return success
    # ---------------- Sample Rate ----------------

    def get_sample_rate(self) -> int:
        sr_bytes = self.transport.ctrl_get(0x2900,0x0200)
        if sr_bytes == b'\x44\xAC\x00\x00':
            return 44100
        elif sr_bytes == b'\x80\xBB\x00\x00':
            return 48000
        elif sr_bytes == b'\x88\x58\x01\x00':
            return 88200
        elif sr_bytes == b'\x00\x77\x01\x00':
            return 96000
        return -1

    def set_sample_rate(self, sr:int) -> bool:
        if sr == 44100:
            sr_bytes = b'\x44\xAC\x00\x00'
        elif sr == 48000:
            sr_bytes = b'\x80\xBB\x00\x00'
        elif sr == 88200:
            sr_bytes = b'\x88\x58\x01\x00'
        elif sr == 96000:
            sr_bytes = b'\x00\x77\x01\x00'
        else:
            print("Invalid sample rate provided.")
            return False
        try:
            self.transport.ctrl_set(0x2900, 0x0200, sr_bytes)
            return True
        except Exception as e:
            self.logger.error(f"Failed to set sample rate: {e}")
            return False

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

    # ---------------- New untested stuff ----------------

    def get_FUI(self) -> Optional[bytes]:
        if not self.device_controlled_by_app:
            return None
        try:
            return self.transport.ctrl_get(EvoProtocol.IDX_FUI, 0x0200, length=4)
        except Exception as e:
            self.logger.error(f"FUI Read Error: {e}")
            return None

    def get_FUO(self) -> Optional[bytes]:
        if not self.device_controlled_by_app:
            return None
        try:
            return self.transport.ctrl_get(EvoProtocol.IDX_FUO, 0x0200, length=4) # also test with 0100
        except Exception as e:
            self.logger.error(f"FUO Read Error: {e}")
            return None


    def get_EULB(self) -> Optional[bytes]:
        if not self.device_controlled_by_app:
            return None
        try:
            return self.transport.ctrl_get(EvoProtocol.IDX_EULB, 0x0300, length=4) # also test with 0100 and 0200
        except Exception as e:
            self.logger.error(f"EULB Read Error: {e}")
            return None

    def get_EULB2(self) -> Optional[bytes]:
        if not self.device_controlled_by_app:
            return None
        try:
            return self.transport.ctrl_get(EvoProtocol.IDX_EULB2, 0x0300, length=4) # also test with 0100 and 0200
        except Exception as e:
            self.logger.error(f"EULB2 Read Error: {e}")
            return None