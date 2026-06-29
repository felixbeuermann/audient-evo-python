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

class UsbNotBoundError(RuntimeError):
    """USB device not bound or already released."""
    def __init__(self, message: str = "USB device not bound or already released"):
        super().__init__(message)   

class DeviceDisconnectedError(RuntimeError):
    def __init__(self, message: str = "USB device disconnected"):
        super().__init__(message)

class EvoUsbTransport:
    """Low-dial USB transport abstraction."""

    def __init__(self, vendor_id: int = 0x2708, product_id: int = 0x0007):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.dev: Optional[usb.core.Device] = None
        self._bound = False
        self._claimed_interfaces: set[int] = set()
        self._detached_interfaces: set[int] = set()

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
            except Exception:
                pass # Ignore errors during release

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

    # ---------------- Internal helpers ----------------

    def _ensure_bound(self) -> None:
        if not self._bound or self.dev is None:
            raise UsbNotBoundError("USB device is not bound")

    def _handle_usb_error(self, error: usb.core.USBError):
        if error.errno in (19, 32):  # ENODEV, EPIPE
            self._bound = False
            self.dev = None
            raise DeviceDisconnectedError("EVO device disconnected") from error
        raise error

    # ---------------- USB control transfers ----------------

    def ctrl_get(self, wValue: int, wIndex: int, length: int = 4) -> bytes:
        self._ensure_bound()
        try:
            return bytes(self.dev.ctrl_transfer(0xA1, 0x01, wValue, wIndex, length))
        except usb.core.USBError as e:
            self._handle_usb_error(e)
            return b"\x00" * length

    def ctrl_set(self, wValue: int, wIndex: int, data: bytes) -> None:
        self._ensure_bound()
        try:
            self.dev.ctrl_transfer(0x21, 0x01, wValue, wIndex, data)
        except usb.core.USBError as e:
            self._handle_usb_error(e)