# ============================================================
# audient_evo/transport.py
# ============================================================
"""
Low-dial USB transport for Audient EVO devices.
Handles device discovery, kernel driver detachment,
and USB control transfers.
"""

from typing import Optional
import usb.core
import usb.util
import time
import atexit
import sys

from audient_evo.protocol import DeviceCapabilities, EVO_PROFILES
from audient_evo.util import UsbNotBoundError, DeviceDisconnectedError, UsbPipeError, UsbTimeoutError, UsbProtocolError

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EvoUsbTransport:
    """Low-dial USB transport abstraction."""

    def __init__(self, dev: usb.core.Device, profile: DeviceCapabilities):
        self.dev = dev
        self.profile = profile
        self._is_connected = False
        self.ghost_mode = True
        self._detached_interfaces: set[int] = set()

        self._setup_graceful_exit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()

    # ---------------- Device Lifecycle ----------------

    @classmethod
    def discover(cls) -> "EvoUsbTransport":
        """
        Scannt den USB-Bus nach Audient EVO Geräten, identifiziert das Modell
        und gibt eine vorkonfigurierte Transport-Instanz zurück.
        """
        # Scanne nach allen Audient-Geräten auf dem Bus
        devices = list(usb.core.find(find_all=True, idVendor=0x2708))

        if not devices:
            raise RuntimeError("Kein Audient USB-Gerät gefunden.")

        for dev in devices:
            pid = dev.idProduct
            if pid in EVO_PROFILES:
                profile = EVO_PROFILES[pid]
                logger.info(f"✨ Audient Hardware erkannt: {profile.name} (PID: {hex(pid)})")
                return cls(dev=dev, profile=profile)

        raise RuntimeError("Audient-Gerät gefunden, aber Modell (PID) ist unbekannt.")

    def connect(self) -> Optional[EvoUsbTransport]:
        if self.dev is None:
            raise RuntimeError("Device is None. was discover() called?")
        # Detach kernel drivers (interfaces 0–3)
        for i in range(4):
            try:
                if self.dev.is_kernel_driver_active(i):
                    self.dev.detach_kernel_driver(i)
                    self._detached_interfaces.add(i)
            except usb.core.USBError as e:
                logger.exception(f"Warning: Could not detach kernel driver on interface {i}: {e}")

        try:
            usb.util.claim_interface(self.dev, 0)
            self._is_connected = True
            self.ghost_mode = False
        except usb.core.USBError as e:
            raise RuntimeError(f"Failed to claim interface 0: {e}")

        return self

    def release(self) -> None:
        if not self._is_connected or self.dev is None:
            return

        for i in self._detached_interfaces:
            try:
                usb.util.release_interface(self.dev, i)
            except usb.core.USBError as e:
                logger.exception(f"Failed to release interface {i}: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error releasing interface {i}: {e}")

        usb.util.dispose_resources(self.dev)

        time.sleep(0.1)

        for i in self._detached_interfaces:
            try:
                if not self.dev.is_kernel_driver_active(i):
                    self.dev.attach_kernel_driver(i)
            except usb.core.USBError as e:
                logger.exception(f"Could not reattach kernel driver on interface {i}: {e}")

        self.dev = None
        self._is_connected = False
        self.ghost_mode = True

    def is_connected(self) -> bool:
        return self.dev is not None and self._is_connected

    def _setup_graceful_exit(self):
        def cleanup(signum=None, frame=None):
            # Attempts to unconditionally release the driver
            try:
                if hasattr(self, 'dev') and self.dev is not None:
                    self.release()
            except Exception as e:
                logger.exception(f"Cleanup non-fatal error: {e}")

            # If triggered by a signal (e.g., Ctrl+C), terminate the script cleanly
            if signum is not None:
                sys.exit(0)

        atexit.register(cleanup)

    # ---------------- Internal helpers ----------------

    def _ensure_bound(self) -> None:
        if self.dev is None: raise UsbNotBoundError()

    def ping(self) -> bool:
        try:
            self.dev.ctrl_transfer( # TODO: CONFIRM THIS WORKS, NOT MANUALLY TESTED
                0x80,
                0x06,
                0x0100,
                0,
                8,
                timeout=100,
            )
            return True

        except usb.core.USBError:
            return False

    def _handle_usb_error(self, e):

        errno = getattr(e, "errno", None)

        if errno == 19:
            self._connected = False
            raise DeviceDisconnectedError("EVO device disconnected") from e

        elif errno == 32:
            raise UsbPipeError("EVO device pipe error") from e

        elif errno == 110:
            raise UsbTimeoutError("EVO timeout") from e

        elif errno == 71:
            raise UsbProtocolError("EVO protocol error") from e

        else:
            raise

    # ---------------- USB control transfers ----------------

    def ctrl_get(self, wValue: int, wIndex: int, length: int = 4, timeout: int = 500) -> bytes:
        if self.ghost_mode:
            return b"\x00" * length
        self._ensure_bound()
        try:
            #print(f"Sending control transfer to EVO device. wValue: {wValue:02X} wIndex: {wIndex:02X} length:{length}")
            return bytes(self.dev.ctrl_transfer(0xA1, 0x01, wValue, wIndex, length, timeout))
        except usb.core.USBError as e:
            self._handle_usb_error(e)
            return b"\x00" * length

    def ctrl_set(self, wValue: int, wIndex: int, data: bytes, timeout: int = 500) -> bool:
        if self.ghost_mode:
            return True
        self._ensure_bound()
        try:
            self.dev.ctrl_transfer(0x21, 0x01, wValue, wIndex, data, timeout)
            return True
        except usb.core.USBError as e:
            self._handle_usb_error(e)
            return False