#!/usr/bin/env python3
"""
SugarCube Menu Bar App
======================
A macOS menu bar application for controlling SweetVinyl SugarCube devices.
Requires sugarcube_client.py to be in the same directory.

Requirements:
    pip install rumps
    sugarcube_client.py must be in the same directory (no other dependencies needed)

Usage:
    python sugarcube_menubar.py

To run as a proper background app (no Terminal window):
    pythonw sugarcube_menubar.py

Devices are loaded from ~/.sugarcube.json (same config as sugarcube_client.py).
"""

import os
import sys
import threading

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

try:
    import rumps
except ImportError:
    sys.stderr.write(
        "ERROR: 'rumps' not found. Install it with:  pip install rumps\n\n"
    )
    sys.exit(1)

# Import the client from the same directory as this script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from sugarcube_client import (
        SugarCubeClient,
        decode_status,
        load_config,
    )
except ImportError:
    rumps.alert(
        "SugarCube",
        "Could not find sugarcube_client.py.\nPlace it in the same folder as this app.",
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL = 10  # seconds between background status polls
ICON_IDLE = "◉"  # menu bar icon when connected and idle
ICON_RECORDING = "⏺"  # menu bar icon when recording
ICON_CLIPPING = "⚡"  # menu bar icon when clipping detected
ICON_ERROR = "⚠"  # menu bar icon when device unreachable


# ---------------------------------------------------------------------------
# Per-device controller
# ---------------------------------------------------------------------------


class DeviceController:
    """Holds a SugarCubeClient and its last-known status for one device."""

    def __init__(self, name: str, url: str, pin: str = None, cookie: str = None):
        self.name = name
        self.sc = SugarCubeClient(url)
        self.status = {}  # last decoded status dict
        self.error = None  # last error string, or None if healthy
        self.clipping = False  # True when device reports active clipping
        self.rec_start = None  # datetime when recording began, or None

        # Authenticate
        if cookie:
            self.sc.load_cookie(cookie)
        elif not self.sc.try_auto_pair():
            if pin:
                self.sc.pair(str(pin))

    def refresh(self):
        """Fetch fresh status from the device. Called from background thread."""
        try:
            raw = self.sc.get_audio_status()
            self.status = decode_status(raw)
            self.error = None
        except Exception as e:
            self.error = str(e)
            return
        try:
            clip_data = self.sc.get_clipping()
            self.clipping = bool(clip_data.get("html"))
        except Exception:
            self.clipping = False


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


class SugarCubeMenuBarApp(rumps.App):

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _build_controllers(self):
        devices = self.config.get("devices", {})
        default = self.config.get("default_device")

        if not devices:
            rumps.alert(
                "SugarCube",
                "No devices found in ~/.sugarcube.json.\n\n"
                "Add one with:\n"
                "  sugarcube_client.py config --add NAME --url URL --pin PIN\n"
                "  sugarcube_client.py config --default NAME",
            )
            rumps.quit_application()
            return

        for name, cfg in devices.items():
            try:
                ctrl = DeviceController(
                    name=name,
                    url=cfg.get("url", ""),
                    pin=cfg.get("pin"),
                    cookie=cfg.get("cookie"),
                )
                self.controllers[name] = ctrl
            except Exception:
                pass  # Device will show as errored on first poll

        # Pick the active device
        if default and default in self.controllers:
            self.active_name = default
        elif self.controllers:
            self.active_name = next(iter(self.controllers))

    def _rec_timer_tick(self, _timer):
        """Update the menu bar title with elapsed recording time every second."""
        import time

        if self._rec_start_time is None:
            return
        elapsed = int(time.monotonic() - self._rec_start_time)
        mins, secs = divmod(elapsed, 60)
        self.title = f"{ICON_RECORDING} {mins}:{secs:02d}"

    # ------------------------------------------------------------------
    # Recording timer
    # ------------------------------------------------------------------

    def _start_rec_timer(self):
        """Begin ticking the elapsed recording time in the menu bar title."""
        import time

        self._rec_start_time = time.monotonic()
        if self._rec_timer is None:
            self._rec_timer = rumps.Timer(self._rec_timer_tick, 1)
            self._rec_timer.start()

    def _stop_rec_timer(self):
        """Stop the recording timer and restore the idle icon."""
        if self._rec_timer is not None:
            self._rec_timer.stop()
            self._rec_timer = None
        self._rec_start_time = None
        self.title = ICON_IDLE

    def _update_menu_from_status(self, ctrl: DeviceController):
        """Push latest status into menu items. Safe to call from any thread."""
        if ctrl.error:
            self.title = ICON_ERROR
            self._status_item.title = "Status: unreachable"
            self._repair_item.title = "Repair: —"
            self._denoise_item.title = "Denoise: —"
            self._recording_item.title = "Recording: —"
            self._samplerate_item.title = "Sample rate: —"
            return

        s = ctrl.status
        rec_state = s.get("rec_state", "idle")

        # ── Recording timer ──────────────────────────────────────────
        if rec_state == "recording" and self._rec_timer is None:
            self._start_rec_timer()
        elif rec_state != "recording" and self._rec_timer is not None:
            self._stop_rec_timer()

        # ── Menu bar icon (only set when timer is not running) ───────
        if self._rec_timer is None:
            self.title = ICON_CLIPPING if ctrl.clipping else ICON_IDLE

        # Status line: device name + route
        self._status_item.title = f"{ctrl.name}  —  {s.get('audio_route', '?')}"

        # Repair
        repair_on = "ON" in s.get("repair_mode", "")
        self._repair_item.title = (
            f"Repair: {'ON ✓' if repair_on else 'OFF'}"
            f"  (sens {s.get('sensitivity', '?')})"
        )
        current_sens = (
            str(int(s["sensitivity"]))
            if s.get("sensitivity") not in ("", "?", None)
            else None
        )
        for item in self._repair_level_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = item.title == current_sens

        # Denoise
        dn_active = s.get("denoise_active") == "Yes"
        self._denoise_item.title = (
            f"Denoise: {'ON ✓' if dn_active else 'OFF'}"
            f"  (level {s.get('denoise_level', '?')})"
        )
        current_dn = (
            str(int(s["denoise_level"]))
            if s.get("denoise_level") not in ("", "?", None)
            else None
        )
        for item in self._denoise_level_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = item.title == current_dn

        # Recording
        self._recording_item.title = f"Recording: {rec_state}"

        # ── Clipping ─────────────────────────────────────────────────
        if ctrl.clipping:
            self._clipping_item.title = "Clipping: DETECTED ⚡"
            self._clear_clipping_item.hidden = False
            # Only notify once per clipping event, not on every poll
            if ctrl.name not in self._clipping_notified:
                self._clipping_notified.add(ctrl.name)
                rumps.notification(
                    "SugarCube",
                    ctrl.name,
                    "Clipping detected! Check your levels.",
                    sound=True,
                )
        else:
            self._clipping_item.title = "Clipping: Clear"
            self._clear_clipping_item.hidden = True
            self._clipping_notified.discard(ctrl.name)

        # Sample rate / bit depth
        self._samplerate_item.title = (
            f"{s.get('sample_rate', '?')}  /  {s.get('bit_depth', '?')}"
        )

        # Update device selector checkmarks
        if len(self.controllers) > 1 and "Device" in self.menu:
            for name, item in self.menu["Device"].items():
                if isinstance(item, rumps.MenuItem):
                    item.state = name == self.active_name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_ctrl(self) -> DeviceController | None:
        return self.controllers.get(self.active_name)

    def _do_poll(self):
        """Refresh the active device and update the menu (thread-safe via rumps)."""
        ctrl = self._active_ctrl()
        if ctrl is None:
            return
        ctrl.refresh()
        # rumps is not thread-safe for UI updates, so schedule on main thread
        rumps.application_support  # touch to ensure app is alive
        self._update_menu_from_status(ctrl)

    def _run_in_bg(self, fn, *args):
        """Run fn(*args) in a background thread so the UI stays responsive."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def _select_device(self, sender):
        self.active_name = sender.title
        self._run_in_bg(self._do_poll)

    def _toggle_repair(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return
        current_on = "ON" in ctrl.status.get("repair_mode", "")

        def do():
            try:
                ctrl.sc.set_click_repair(enabled=not current_on)
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _toggle_denoise(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return
        current_on = ctrl.status.get("denoise_active") == "Yes"

        def do():
            try:
                ctrl.sc.set_noise_reduction(enabled=not current_on)
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _set_repair_level(self, sender):
        ctrl = self._active_ctrl()
        if not ctrl:
            return
        value = int(sender.title)

        def do():
            try:
                ctrl.sc.set_click_repair(sensitivity=value)
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _set_denoise_level(self, sender):
        ctrl = self._active_ctrl()
        if not ctrl:
            return
        value = int(sender.title)

        def do():
            try:
                ctrl.sc.set_noise_reduction(level=value)
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _learn_noise(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return

        def do():
            try:
                ctrl.sc.sample_noise(start=True)
                rumps.notification("SugarCube", ctrl.name, "Noise learning started.")
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _stop_learn_noise(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return

        def do():
            try:
                ctrl.sc.sample_noise(start=False)
                rumps.notification("SugarCube", ctrl.name, "Noise learning stopped.")
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _start_recording(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return

        def do():
            try:
                ctrl.sc.start_recording()
                rumps.notification("SugarCube", ctrl.name, "Recording started.")
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _stop_recording(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return

        def do():
            try:
                ctrl.sc.stop_recording()
                rumps.notification("SugarCube", ctrl.name, "Recording stopped.")
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            self._stop_rec_timer()
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _clear_clipping(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return

        def do():
            try:
                ctrl.sc.clear_clipping()
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))
            ctrl.refresh()
            self._update_menu_from_status(ctrl)

        self._run_in_bg(do)

    def _refresh_now(self, _):
        self._run_in_bg(self._do_poll)

    def _identify(self, _):
        ctrl = self._active_ctrl()
        if not ctrl:
            return

        def do():
            try:
                ctrl.sc.identify()
                rumps.notification("SugarCube", ctrl.name, "Identify command sent.")
            except Exception as e:
                rumps.notification("SugarCube", "Error", str(e))

        self._run_in_bg(do)

    def _quit(self, _):
        rumps.quit_application()

    def _build_menu(self):
        """Construct the full menu structure."""
        self.menu.clear()

        if not self.controllers:
            self.menu.add(rumps.MenuItem("No devices configured"))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Quit", callback=self._quit))
            return

        # ── Device selector (only shown when >1 device) ──────────────
        if len(self.controllers) > 1:
            device_menu = rumps.MenuItem("Device")
            for name in self.controllers:
                item = rumps.MenuItem(name, callback=self._select_device)
                device_menu.add(item)
            self.menu.add(device_menu)
            self.menu.add(rumps.separator)

        # ── Status (read-only, updated by poller) ────────────────────
        self._status_item = rumps.MenuItem("Status: —")
        self._repair_item = rumps.MenuItem("Repair: —")
        self._denoise_item = rumps.MenuItem("Denoise: —")
        self._recording_item = rumps.MenuItem("Recording: —")
        self._samplerate_item = rumps.MenuItem("Sample rate: —")
        self._clipping_item = rumps.MenuItem("Clipping: —")

        for item in (
            self._status_item,
            self._repair_item,
            self._denoise_item,
            self._recording_item,
            self._samplerate_item,
            self._clipping_item,
        ):
            item.set_callback(None)  # display-only, not clickable
            self.menu.add(item)

        self._clear_clipping_item = rumps.MenuItem(
            "Clear Clipping", callback=self._clear_clipping
        )
        self._clear_clipping_item.hidden = True
        self.menu.add(self._clear_clipping_item)

        self.menu.add(rumps.separator)

        # ── Click Repair submenu ─────────────────────────────────────
        repair_menu = rumps.MenuItem("Click Repair")
        repair_menu.add(rumps.MenuItem("Toggle Repair", callback=self._toggle_repair))
        repair_level_menu = rumps.MenuItem("Repair Level")
        for i in range(1, 11):
            repair_level_menu.add(
                rumps.MenuItem(str(i), callback=self._set_repair_level)
            )
        repair_menu.add(repair_level_menu)
        self._repair_level_menu = repair_level_menu
        self.menu.add(repair_menu)

        # ── Denoise submenu ──────────────────────────────────────────
        denoise_menu = rumps.MenuItem("Denoise")
        denoise_menu.add(
            rumps.MenuItem("Toggle Denoise", callback=self._toggle_denoise)
        )
        denoise_level_menu = rumps.MenuItem("Denoise Level")
        for i in range(1, 11):
            denoise_level_menu.add(
                rumps.MenuItem(str(i), callback=self._set_denoise_level)
            )
        denoise_menu.add(denoise_level_menu)
        self._denoise_level_menu = denoise_level_menu
        denoise_menu.add(rumps.MenuItem("Learn Noise", callback=self._learn_noise))
        denoise_menu.add(
            rumps.MenuItem("Stop Learning", callback=self._stop_learn_noise)
        )
        self.menu.add(denoise_menu)

        self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem("Start Recording", callback=self._start_recording))
        self.menu.add(rumps.MenuItem("Stop Recording", callback=self._stop_recording))

        self.menu.add(rumps.separator)

        # ── Utility ─────────────────────────────────────────────────
        self.menu.add(rumps.MenuItem("Refresh Now", callback=self._refresh_now))
        self.menu.add(rumps.MenuItem("Identify Device", callback=self._identify))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit SugarCube", callback=self._quit))

    def _poll_tick(self, _timer):
        """Called by rumps.Timer on the main thread — spawns a worker."""
        threading.Thread(target=self._do_poll, daemon=True).start()

    # ------------------------------------------------------------------
    # Background polling
    # ------------------------------------------------------------------

    def _start_poller(self):
        """Kick off a repeating background thread that refreshes device status."""
        self._poll_timer = rumps.Timer(self._poll_tick, POLL_INTERVAL)
        self._poll_timer.start()
        # Also do an immediate first poll
        threading.Thread(target=self._do_poll, daemon=True).start()

    def __init__(self):
        super().__init__("SugarCube", title=ICON_IDLE, quit_button=None)

        self.config = load_config()
        self.controllers = {}  # name -> DeviceController
        self.active_name = None  # currently selected device name

        # Clipping: track per-device so we only notify on the rising edge
        self._clipping_notified = set()  # device names already notified

        # Recording timer
        self._rec_timer = None  # rumps.Timer instance, or None
        self._rec_start_time = None  # time.monotonic() when recording began

        self._build_controllers()
        self._build_menu()
        self._start_poller()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    SugarCubeMenuBarApp().run()
