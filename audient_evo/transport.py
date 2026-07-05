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

from audient_evo.util import UsbNotBoundError, DeviceDisconnectedError, UsbPipeError, UsbTimeoutError, UsbProtocolError

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EvoUsbTransport:
    """Low-dial USB transport abstraction."""

    def __init__(self, vendor_id: int = 0x2708, product_id: int = 0x0007):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.dev: Optional[usb.core.Device] = None
        self._bound = False
        self._claimed_interfaces: set[int] = set()
        self._detached_interfaces: set[int] = set()

        self._setup_graceful_exit()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()

    # ---------------- Device Lifecycle ----------------

    def connect(self) -> Optional[EvoUsbTransport]:
        self.dev = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)
        if self.dev is None:
            raise RuntimeError(f"EVO device (VID:{self.vendor_id:04X}, PID:{self.product_id:04X}) not found")

        # Detach kernel drivers (interfaces 0–3)
        for i in range(4):
            try:
                if self.dev.is_kernel_driver_active(i):
                    self.dev.detach_kernel_driver(i)
                    self._detached_interfaces.add(i)
            except usb.core.USBError as e:
                print(f"Warning: Could not detach kernel driver on interface {i}: {e}")

        try:
            usb.util.claim_interface(self.dev, 0)
            self._bound = True
        except usb.core.USBError as e:
            raise RuntimeError(f"Failed to claim interface 0: {e}")

        return self

    def release(self) -> None:
        if not self._bound or self.dev is None:
            return

        # 1. Release claimed interfaces
        for i in self._detached_interfaces:
            try:
                usb.util.release_interface(self.dev, i)
            except usb.core.USBError as e:
                logger.warning(f"Failed to release interface {i}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error releasing interface {i}: {e}")

        # 2. Dispose libusb resources
        usb.util.dispose_resources(self.dev)

        # 3. Small delay to avoid race with PipeWire / ALSA
        time.sleep(0.1)

        # 4. Reattach kernel driver
        for i in self._detached_interfaces:
            try:
                if not self.dev.is_kernel_driver_active(i):
                    self.dev.attach_kernel_driver(i)
            except usb.core.USBError as e:
                print(f"Could not reattach kernel driver on interface {i}: {e}")

        self.dev = None
        self._bound = False

    def is_connected(self) -> bool:
        return self.dev is not None and self._bound

    def _setup_graceful_exit(self):
        def cleanup(signum=None, frame=None):
            # Versucht bedingungslos, den Treiber wieder freizugeben
            try:
                self.release()
            except Exception:
                pass

            # Wenn durch ein Signal (z.B. Ctrl+C) ausgelöst, Skript sauber beenden
            if signum is not None:
                sys.exit(0)

        atexit.register(cleanup)


    # ---------------- Internal helpers ----------------

    def _ensure_bound(self) -> None:
        if self.dev is None:
            raise UsbNotBoundError("USB device is None")

        try:
            # Einfacher Deskriptorzugriff
            _ = self.dev.idVendor # vendor_id?
            _ = self.dev.idProduct

        except ValueError:
            raise DeviceDisconnectedError("USB device object invalid")

        except usb.core.USBError as e:
            self._handle_usb_error(e)

    def ping(self) -> bool:
        try:
            self.dev.ctrl_transfer(
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

    def ctrl_get(self, wValue: int, wIndex: int, length: int = 4) -> bytes:
        self._ensure_bound()
        try:
            #print(f"Sending control transfer to EVO device. wValue: {wValue:02X} wIndex: {wIndex:02X} length:{length}")
            return bytes(self.dev.ctrl_transfer(0xA1, 0x01, wValue, wIndex, length))
        except usb.core.USBError as e:
            self._handle_usb_error(e)
            #return b"\x00" * length

    def ctrl_set(self, wValue: int, wIndex: int, data: bytes) -> bool:
        self._ensure_bound()
        try:
            self.dev.ctrl_transfer(0x21, 0x01, wValue, wIndex, data)
            return True
        except usb.core.USBError as e:
            self._handle_usb_error(e)
            return False