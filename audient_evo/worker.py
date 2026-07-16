# ============================================================
# audient_evo/worker.py
# ============================================================
"""
Background worker threads for Audient EVO 8.
Handles asynchronous hardware event listening and watchdogs.
"""

import threading
import time
import logging
from typing import Optional

from .protocol import HARDWARE_TO_CATEGORY
from .util import (
    bytes_to_gain, bytes_to_bool, bytes_to_volume,
    DeviceDisconnectedError, UsbNotBoundError
)

logger = logging.getLogger(__name__)


class EvoBackgroundWorker:
    """Manages the background threads for hardware events and watchdogs."""

    def __init__(self, device, state_manager, lock: threading.RLock):
        self.device = device
        self.state = state_manager
        self.lock = lock

        self._running = False
        self._event_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Starts the background threads."""
        if self._running:
            return

        self._running = True

        # Event Listener Thread (listens for USB interrupts/reports)
        self._event_thread = threading.Thread(target=self._event_loop, name="EvoEventThread", daemon=True)
        self._event_thread.start()

        # Watchdog Thread (keeps the USB connection alive)
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name="EvoWatchdogThread", daemon=True)
        self._watchdog_thread.start()

        logger.info("EVO background workers successfully started.")

    def stop(self) -> None:
        """Stops the threads cleanly."""
        self._running = False
        logger.info("EVO background workers stopped.")

    def _event_loop(self) -> None:
        """Listens for hardware events (e.g., physical knob movements)."""
        while self._running:
            try:
                # Here we call the event_listen of your transport.
                # Since event_listen can block, we only acquire the lock WHEN data is available!
                raw_buffer = self.device.event_listen()

                if raw_buffer:
                    with self.lock:
                        self._sync_cache_from_hardware(raw_buffer)

            except (DeviceDisconnectedError, UsbNotBoundError) as e:
                logger.exception(f"Event loop lost connection to device: {e}")
                break
            except Exception as e:
                logger.exception(f"Error in event loop: {e}")
                time.sleep(0.1)

            time.sleep(0.01)

    def _watchdog_loop(self) -> None:
        """Sends regular keep-alive pings to the hardware, if necessary."""
        while self._running:
            try:
                with self.lock:
                    # Example of a keep-alive ping (if your transport supports it)
                    if hasattr(self.device.transport, 'ping'):
                        self.device.transport.ping()
            except Exception as e:
                logger.exception(f"Watchdog ping failed: {e}")

            # ping every 2 seconds
            for _ in range(20):
                if not self._running: break
                time.sleep(0.1)

    def _sync_cache_from_hardware(self, raw_buffer: bytes) -> None:
        """Interprets the raw USB event bytes and updates the StateManager."""
        if not raw_buffer or len(raw_buffer) < 4:
            logger.warning(f"Malformed event buffer received: {raw_buffer}")
            return

        # Reconstruction of your original event parsing logic
        unit = raw_buffer[0]
        selector = raw_buffer[1]
        ch = raw_buffer[2] + 1  # 0-based auf 1-based konvertieren
        val_bytes = raw_buffer[3:]

        category = HARDWARE_TO_CATEGORY.get((unit, selector))
        if not category:
            return

        try:
            if category == "gain":
                value = bytes_to_gain(val_bytes)
                self.state.update_input(ch, "gain", value)
            elif category == "phantom":
                value = bytes_to_bool(val_bytes)
                self.state.update_input(ch, "phantom", value)
            elif category == "mic_mute":
                value = bytes_to_bool(val_bytes)
                self.state.update_input(ch, "mic_mute", value)
            elif category == "mic_mono":
                value = bytes_to_bool(val_bytes)
                self.state.update_input(ch, "mono", value)
            elif category == "volume":
                value = bytes_to_volume(val_bytes)
                self.state.update_output(ch, "volume", value)

            logger.debug(f"Hardware Sync: {category} for Ch {ch} set to {val_bytes.hex()}.")
        except Exception as e:
            logger.exception(f"Error parsing hardware event ({category}): {e}")