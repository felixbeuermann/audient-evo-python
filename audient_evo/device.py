# ============================================================
# audient_evo/device.py
# ============================================================
"""
High-dial EVO 8 device API.
This is the primary interface intended for UI and scripting.
"""
import time
from typing import Optional, Callable, Any
import threading
from functools import wraps

import usb

from .protocol import LOOPBACK_SOURCES, SAMPLE_RATES, \
    SAMPLE_RATE_INV, LOOPBACK_MAPPINGS_INV, LOOPBACK_TARGETS, CATEGORY_TO_HARDWARE
from .transport import EvoUsbTransport
from .state import EvoStateManager
from .worker import EvoBackgroundWorker
from .util import gain_to_bytes, percent_to_gain_step, mon_value_to_bytes, \
    percent_to_mon_step, bytes_to_gain, gain_step_to_percent, bytes_to_mon_value, \
    bytes_to_volume, is_in_range, out_step_to_percent, volume_to_bytes,\
    percent_to_out_step, DeviceDisconnectedError, UsbNotBoundError, \
    get_partner_channel, calculate_monitor_wValue, mon_step_to_percent

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def safe_usb_transaction(func: Callable) -> Callable:
    """
    Decorator that combines thread safety (Lock) AND error handling (Try/Catch)
    for USB transactions.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs) -> Any:
        # 1. lock Thread
        with self._lock:
            # 2. Try-Catch for USB-Transactions
            try:
                return func(self, *args, **kwargs)
            except usb.core.USBError as e:
                # Logs the error along with the name of the function that failed!
                logger.exception(f"Hardware-Fehler in '{func.__name__}': {e}")
                # If it's a setter (normally returns bool), return False
                if func.__name__.startswith("set_"):
                    return False
                # If it's a getter, return None or -1
                return -1

    return wrapper

class Evo8Device:
    """High-dial user-facing device API."""

    def __init__(self, transport: EvoUsbTransport):
        self._last_state: Optional[bytes] = None
        self.last_error: Optional[str] = None

        self.NUM_INPUTS = 4
        self.NUM_OUTPUTS = 4
        self.NUM_MONITOR_INPUTS = 10

        self.transport = transport

        self.device_controlled_by_app = True

        # 1. The central RLock (Reentrant Lock) for absolute thread safety
        self._lock = threading.RLock()

        # 2. Initialize sub-systems
        self.state = EvoStateManager()
        self.worker = EvoBackgroundWorker(self, self.state, self._lock)

        # Optional: Query the hardware once at startup
        # to initially populate the cache (Initial Sync)
        if self._initialize_state_from_hardware():
            # Start background threads
            self.worker.start()

    def close(self) -> None:
        """Shuts down the device and stops all threads cleanly."""
        self.worker.stop()
        with self._lock:
            self.transport.release()
        logger.info("EVO 8 Device successfully closed.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

        # ---------------- INITIALISIERUNG ----------------

    def _initialize_state_from_hardware(self) -> bool:
        """Reads all important values live from the device at startup to populate the cache."""
        logger.info("Synchronizing initial state from hardware...")
        success = True
        try:
            for ch in range(1, self.NUM_INPUTS+1):
                gain = self.get_gain(ch)
                self.state.update_input(ch, "gain", gain)
                phantom = self.get_phantom(ch)
                self.state.update_input(ch, "phantom", phantom)
                mic_mute = self.get_mic_mute(ch)
                self.state.update_input(ch, "mic_mute", mic_mute)
                mic_mono = self.get_mic_mono(ch)
                self.state.update_input(ch, "stereo_link", mic_mono)

            for ch in range (1, self.NUM_OUTPUTS+1):
                volume = self.get_volume(ch)
                self.state.update_output(ch, "volume", volume)
                out_mute = self.get_out_mute(ch)
                self.state.update_output(ch, "out_mute", out_mute)
                out_stereo = self.get_out_stereo(ch)
                self.state.update_output(ch, "stereo_link", out_stereo)


            self.set_monitor(0, 10, 20) # Wakeup monitor by calling non-existent monitor address
            for in_ch in range(1, self.NUM_MONITOR_INPUTS+1):
                for out_ch in range(1, self.NUM_OUTPUTS+1):
                    monitor = self.get_monitor(in_ch, out_ch)
                    self.state.update_monitor(in_ch, out_ch, "volume", monitor)
                    time.sleep(0.01)

            loopback_source = self.get_loopback("LB1+2")
            if loopback_source not in LOOPBACK_SOURCES:
                loopback_source = self.get_loopback("PC1+2")
                self.state.update_global("loopback_target", "PC1+2")
            elif loopback_source not in LOOPBACK_SOURCES:
                loopback_source = self.get_loopback("PC3+4")
                self.state.update_global("loopback_target", "PC3+4")
            else:
                self.state.update_global("loopback_target", "LB1+2")
            time.sleep(0.02)
            sample_rate =self.get_sample_rate()

            self.state.update_global("loopback_source", loopback_source)
            self.state.update_global("sample_rate", sample_rate)

            return success
        except Exception as e:
            logger.exception(f"Init state from hardware failed: {e}")
        return False

    # --------------------------------------------------------
    # Control ownership
    # --------------------------------------------------------

    def toggle_driver_control(self) -> bool:
        try:
            if self.transport.ping(): # Probably not necessary to check for a response here
                self.transport.release()
            else:
                self.transport.connect()
            self.device_controlled_by_app = not self.device_controlled_by_app
            return True
        except (DeviceDisconnectedError, UsbNotBoundError):
            logger.warning("Device disconnected during control toggle")
            return False




    def _set_parameter(self, param_name: str, data: bytes, ch: Optional[int] = None, out_ch: Optional[int] = None) -> bool:
        """Central function for sending USB values based on the dictionary."""
        mapping = CATEGORY_TO_HARDWARE.get(param_name)
        if not mapping:
            logger.error(f"Unknown parameter: {param_name}")
            return False
        # Case 1: Monitor Matrix (requires in_ch and out_ch)
        if ch is not None and out_ch is not None and param_name == "monitor":
            wValue = calculate_monitor_wValue(mapping["wValue_base"], ch, out_ch)
        # Case 2: Regular channel (1-based to 0-based offset)
        elif ch is not None:
            wValue = mapping["wValue_base"] + (ch - 1)
        # Case 3: Global parameters (like monitor_bridge without channel)
        else:
            wValue = mapping["wValue_base"]

        return self.transport.ctrl_set(wValue, mapping["wIndex"], data)


    def _get_parameter(self, param_name: str, ch: Optional[int] = None, out_ch: Optional[int] = None) -> bytes:
        """Central function for querying USB values based on the dictionary."""
        mapping = CATEGORY_TO_HARDWARE.get(param_name)
        if not mapping:
            logger.error(f"Unknown parameter: {param_name}")
            return b""

        if out_ch is not None and param_name == "monitor" and ch is not None:
            wValue = calculate_monitor_wValue(mapping["wValue_base"], ch, out_ch)
        elif ch is not None:
            wValue = mapping["wValue_base"] + (ch - 1)
        else:
            wValue = mapping["wValue_base"]

        return self.transport.ctrl_get(wValue, mapping["wIndex"], length=mapping["length"])


    # ---------------- Input controls ----------------

    @safe_usb_transaction
    def set_phantom(self, ch: int, state: bool) -> bool:
        state_byte = state.to_bytes(1)
        success = self._set_parameter("phantom", state_byte, ch)
        if success:
            self.state.update_input(ch, "phantom", state)
        return success

    @safe_usb_transaction
    def get_phantom(self, ch: int) -> bool:
        state_byte = self._get_parameter("phantom", ch)
        if state_byte is None:
            logger.error(f"get_phantom: state_byte is None.")
            return False
        return bool(state_byte)

    @safe_usb_transaction
    def set_gain(self, ch: int, value: int) -> bool:
        if not is_in_range(value):
            logger.error(f"set_gain: Invalid gain value {value}")
            return False
        gain_bytes = gain_to_bytes(percent_to_gain_step(value))

        success = self._set_parameter("gain", gain_bytes, ch)

        if success:
            self.state.update_input(ch, "gain", value)
        return success

    @safe_usb_transaction
    def get_gain(self, ch: int) -> int:
        if ch not in range(1, self.NUM_INPUTS+1): #TODO: REMOVE FROM HERE BUT MAKE GLOBAL WITHOUT REPEATING IT 200 TIMES
            gain_bytes = self._get_parameter("gain", ch)
            if gain_bytes is None:
                return -1

            return gain_step_to_percent(bytes_to_gain(gain_bytes))
        return -1

    @safe_usb_transaction
    def set_mic_mute(self, ch: int, state: bool) -> bool:
        state_byte = state.to_bytes(1) # TODO MAKE BOOL_TO_BYTE RETURN 1 BYTE NOT 4!!!!!!!!!#########################################################################################################
        success = self._set_parameter("mic_mute", state_byte, ch)
        if success:
            self.state.update_input(ch, "mic_mute", state)
        return success

    @safe_usb_transaction
    def get_mic_mute(self, ch: int) -> bool:
        state_byte = self._get_parameter("mic_mute", ch)
        if state_byte is None:
            logger.error(f"get_mic_mute: state_byte is None.")
            return False
        return bool(state_byte)

    @safe_usb_transaction
    def set_mic_mono(self, ch: int, state: bool) -> bool:
        state_byte = state.to_bytes(1)
        success = self._set_parameter("mic_mono", state_byte, ch)
        if success:
            self.state.update_input(ch, "stereo_link", state)
        return success

    @safe_usb_transaction
    def get_mic_mono(self, ch: int) -> bool:
        state_byte = self._get_parameter("mic_mono", ch)
        if state_byte is None:
            logger.error(f"get_mono: state_byte is None.")
            return False
        return bool(state_byte)

    # ---------------- Output controls ----------------

    @safe_usb_transaction
    def set_volume(self, volume: int, out_ch: int) -> bool:
        if not is_in_range(volume):
            logger.error(f"set_volume: Invalid volume {volume}")
            return False

        volume_bytes = volume_to_bytes(percent_to_out_step(volume))
        success = self._set_parameter("volume", volume_bytes, ch=out_ch)

        if success:
            self.state.update_output(out_ch, "volume", volume)

            if self.state.get_output(out_ch, "stereo_link"):
                partner = get_partner_channel(out_ch)
                self.state.update_output(partner, "volume", volume)

        return success


    @safe_usb_transaction
    def get_volume(self, out_ch: int) -> int:
        vol_bytes = self._get_parameter("volume", ch=out_ch)

        if not vol_bytes or len(vol_bytes) < 4:
            return -1

        volume = out_step_to_percent(bytes_to_volume(vol_bytes))    # appears to work
        return volume

    @safe_usb_transaction
    def set_out_mute(self, state: bool, out_ch: int) -> bool:
        state_byte = state.to_bytes(1)
        success = self._set_parameter("out_mute", state_byte, out_ch)

        if success:
            self.state.update_output(out_ch, "mute", state)

            if self.state.get_output(out_ch, "stereo_link"):
                partner = get_partner_channel(out_ch)
                self.state.update_output(partner, "mute", state)

        return success

    @safe_usb_transaction
    def get_out_mute(self, out_ch: int) -> bool:
        state_byte = self._get_parameter("out_mute", out_ch)
        return bool(state_byte)

    @safe_usb_transaction
    def set_out_stereo(self, out_ch: int, enable: bool) -> bool:
        """
        Toggles Mono/Stereo.
        ch: The channel from which the action originates (important when enabling the link!)
        """
        state_byte = enable.to_bytes(1)
        # Send the link command (0x0200)
        success = self._set_parameter("out_stereo", state_byte, out_ch)

        if success:
            partner = get_partner_channel(out_ch)

            # 1. We update the link status for both channels in the cache
            self.state.update_output(out_ch, "stereo_link", enable)
            self.state.update_output(partner, "stereo_link", enable)

            # 2. When linking, the hardware copies the volume from 'ch' to 'partner'.
            # Our cache must now reflect this!
            if enable:
                current_vol = self.state.get_output(out_ch, "volume")
                if current_vol != -1:
                    self.state.update_output(partner, "volume", current_vol)

        return success

    @safe_usb_transaction
    def get_out_stereo(self, out_ch: int) -> bool:
        state_byte = self._get_parameter("out_stereo", out_ch)
        return bool(state_byte)

    # ---------------- Monitor Mixer ----------------

    @safe_usb_transaction
    def set_monitor(self, value: int, in_ch: int, out_ch: int) -> bool:
        if not is_in_range(value):
            logger.error(f"set_gain: Invalid gain value {value}")
            return False
        monitor_bytes = mon_value_to_bytes(percent_to_mon_step(value))

        success = self._set_parameter("monitor", monitor_bytes, in_ch, out_ch)
        if success:
            self.state.update_monitor(in_ch, out_ch, "volume", value)

            if self.state.get_output(out_ch, "stereo_link"):
                partner = get_partner_channel(out_ch)
                self.state.update_monitor(in_ch, partner, "volume", value)

            if self.state.get_input(in_ch, "stereo_link"):
                partner = get_partner_channel(in_ch)
                self.state.update_monitor(partner, out_ch, "volume", value)

        return success


    @safe_usb_transaction
    def get_monitor(self, in_ch: int, out_ch: int) -> int:
        monitor_vol_bytes = self._get_parameter("monitor", in_ch, out_ch)

        if monitor_vol_bytes == b'\x00\x00\xff\xff':
            return 0
        else:
            monitor_vol = mon_step_to_percent(bytes_to_mon_value(monitor_vol_bytes))
        return monitor_vol

    @safe_usb_transaction
    def set_monitor_bridge(self, enable: bool) -> bool:
        # 1 Byte boolean!
        data = b'\x01' if enable else b'\x00'
        # Kein 'ch' notwendig!
        return self._set_parameter("monitor_bridge", data)

    @safe_usb_transaction
    def get_monitor_bridge(self) -> bool:
        mon_bridge_bytes = self._get_parameter("monitor_bridge") #TODO: THIS IS VERY EXPERIMENTAL
        return bool(mon_bridge_bytes)

    # ---------------- Loopback ----------------

    @safe_usb_transaction
    def get_loopback(self, loopback_target: str) -> str:
        if loopback_target not in LOOPBACK_TARGETS:
            raise ValueError(f"Invalid loopback target. Supported: {list(LOOPBACK_TARGETS.keys())}")

        # Unpack addresses from the dictionary
        wValue_left, wValue_right = LOOPBACK_TARGETS[loopback_target]

        # Query values from the hardware
        loopback_byte_left = self.transport.ctrl_get(wValue_left, 0x3300, length=1)
        loopback_byte_right = self.transport.ctrl_get(wValue_right, 0x3300, length=1)

        return LOOPBACK_MAPPINGS_INV.get((loopback_byte_left, loopback_byte_right), "Unknown loopback group")

    @safe_usb_transaction
    def set_loopback(self, loopback_target: str, loopback_source: str) -> bool:
        if loopback_source not in LOOPBACK_SOURCES:
            raise ValueError(f"Invalid loopback source. Supported: {list(LOOPBACK_SOURCES.keys())}")
        if loopback_target not in LOOPBACK_TARGETS:
            raise ValueError(f"Invalid loopback target. Supported: {list(LOOPBACK_TARGETS.keys())}")

        # Cleanly unpack addresses and data bytes from the dictionaries
        wValue_left, wValue_right = LOOPBACK_TARGETS[loopback_target]
        data_left, data_right = LOOPBACK_SOURCES[loopback_source]

        # Write both channels
        success = self.transport.ctrl_set(wValue_left, 0x3300, data_left)
        if success:
            success = self.transport.ctrl_set(wValue_right, 0x3300, data_right)

        if success:
            self.state.update_global("loopback_source", loopback_source)
            self.state.update_global("loopback_target", loopback_target)

        return success

    # ---------------- Sample Rate ----------------

    @safe_usb_transaction
    def get_sample_rate(self) -> int:
        sr_bytes = self.transport.ctrl_get(0x0100,0x2900, 4) #self._get_parameter("sample_rate")
        return SAMPLE_RATE_INV.get(sr_bytes, -1)

    @safe_usb_transaction
    def set_sample_rate(self, sr:int) -> bool:
        if sr not in SAMPLE_RATES:
            raise ValueError(f"Unsupported sample rate {sr}. Supported: {list(SAMPLE_RATES.keys())}")
        success = self.transport.ctrl_set(0x0100, 0x2900, SAMPLE_RATES[sr])
        if success:
            self.state.update_global("sample_rate", sr)
        return success

    # ---------------- Events ----------------

    #@safe_usb_transaction # locking here might freeze when trying to react to changes (set_vol etc)
    def event_listen(self) -> Optional[bytes]:
        return self.transport.ctrl_get(0x0600, 0x3E00, 4, 500)

    def event_changed(self, new_state: bytes) -> bool:
        if new_state != self._last_state:
            self._last_state = new_state
            return True
        return False