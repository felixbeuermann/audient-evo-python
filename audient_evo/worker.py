# ============================================================
# audient_evo/worker.py
# ============================================================
"""
Background worker threads for Audient EVO 8.
Handles asynchronous hardware event listening and watchdogs.
"""

import threading
import queue
import logging

from .protocol import HARDWARE_TO_CATEGORY
from .util import split_monitor_channel

logger = logging.getLogger(__name__)

class EvoBackgroundWorker:
    """Manages the background threads for hardware events and watchdogs."""

    def __init__(self, device, state_manager):
        self.device = device
        self.state = state_manager
        self._running = False
        self._smart_thread = None

    def start(self) -> None:
        """Starts the background threads."""
        if self._running: return
        self._running = True
        self._smart_thread = threading.Thread(target=self._usb_event_loop, name="EvoSmartUsbThread", daemon=True)
        self._smart_thread.start()
        logger.info("EVO Background Worker started (Smart Queue/Polling).")

    def stop(self) -> None:
        """Stops the threads cleanly."""
        self._running = False
        if self._smart_thread and self._smart_thread.is_alive():
            self._smart_thread.join(timeout=1.0)
        logger.info("EVO Background Worker stopped.")

    def _usb_event_loop(self):
        # Mark the thread: only it can execute USB commands directly
        threading.current_thread().is_usb_worker = True

        while self._running:
            try:
                # Highest Priority: handle GUI-Events
                task = self.device.command_queue.get(timeout=0.01)
                try:
                    result = task.func(*task.args, **task.kwargs)
                    task.future.set_result(result)
                except Exception as e:
                    task.future.set_exception(e)
                finally:
                    self.device.command_queue.task_done()

            except queue.Empty:
                # Idle: When GUI is silent, poll for events
                try:
                    raw_buffer = self.device.event_listen()
                    if raw_buffer:
                        self._sync_cache_from_hardware(raw_buffer)
                except Exception:
                    # Ignore timeout when polling
                    pass

    def _sync_cache_from_hardware(self, raw_buffer: bytes) -> None:
        """Interprets the raw USB event bytes and updates the StateManager."""
        if not raw_buffer or len(raw_buffer) < 4:
            logger.warning(f"Malformed event buffer received: {raw_buffer}")
            return

        selector = raw_buffer[0]
        ch = raw_buffer[1] + 1  # 0-based auf 1-based konvertieren
        unit = raw_buffer[3]    # 2 is irrelevant in this case as endpoint 0 is always used

        category = HARDWARE_TO_CATEGORY.get((unit, selector))
        if not category:
            return

        try:
            if category == "gain":
                value = self.device.get_gain_db(ch)
                self.state.update_input(ch, "gain", value)
            elif category == "phantom":
                value = self.device.get_phantom(ch)
                self.state.update_input(ch, "phantom", value)
            elif category == "mic_mute":
                value = self.device.get_mic_mute(ch)
                self.state.update_input(ch, "mute", value)
            elif category == "mic_stereo":
                value = self.device.get_mic_stereo(ch)
                self.state.update_input(ch, "stereo_link", value)
            elif category == "volume":
                value = self.device.get_volume_db(ch)
                self.state.update_output(ch, "volume", value)
            elif category == "out_mute":
                value = self.device.get_out_mute(ch)
                self.state.update_output(ch, "mute", value)
            elif category == "out_stereo":
                value = self.device.get_out_stereo(ch)
                self.state.update_output(ch, "stereo_link", value)
            elif category == "monitor":
                in_ch, out_ch = split_monitor_channel(ch)
                value = self.device.get_monitor_db(in_ch, out_ch)
                self.state.update_monitor(in_ch, out_ch, "volume", value)

            #elif category == "sample_rate":
            #    value = self.device.get_sample_rate()
            #    self.state.update_global("sample_rate", value)

            #elif category == "loopback_left":
            #    value = self.device.get_loopback_source()
            #    self.state.update_global("", value)
            # elif category == "loopback_right":

            logger.debug(f"Hardware Sync: {category} for Ch {ch} updated.")
        except Exception as e:
            logger.exception(f"Error parsing hardware event ({category}): {e}")