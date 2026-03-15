#!/usr/bin/env python3
"""
SweetVinyl SugarCube Python Client
===================================
Communicates with one or more SugarCube devices over their HTTP API.
Supports authentication (pairing), status queries, parameter changes,
a polling monitor, and an optional curses TUI.

Usage:
    python sugarcube_client.py --help

Config file (~/.sugarcube.json):
    {
        "devices": {
            "living_room": {
                "url": "http://192.168.1.50",
                "pin": "1234",
                "cookie": "previously-saved-scauth-value"
            },
            "studio": {
                "url": "http://192.168.1.51",
                "pin": "5678"
            }
        },
        "default_device": "living_room",
        "default_interval": 5,
        "timeout": 10
    }

Requirements:
    Python 3.9+ standard library only — no third-party packages needed.
"""

import argparse
import curses
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.cookiejar import CookieJar
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener, HTTPCookieProcessor


CONFIG_PATH = os.path.expanduser("~/.sugarcube.json")

# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------


def load_config() -> dict:
    """Load config from ~/.sugarcube.json, returning an empty dict if not found."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Could not read config file {CONFIG_PATH}: {e}")
    return {}


def save_config(config: dict):
    """Save config to ~/.sugarcube.json."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logging.debug(f"Config saved to {CONFIG_PATH}")
    except OSError as e:
        logging.error(f"Could not write config file {CONFIG_PATH}: {e}")


def config_save_cookie(config: dict, device_name: str, cookie: str):
    """Persist an scauth cookie back into the config for a named device."""
    config.setdefault("devices", {}).setdefault(device_name, {})["cookie"] = cookie
    save_config(config)


# ---------------------------------------------------------------------------
# HTTP error helper
# ---------------------------------------------------------------------------


class HTTPStatusError(Exception):
    """Raised when the server returns a non-2xx status code."""

    def __init__(self, status: int, reason: str, body: bytes = b""):
        super().__init__(f"HTTP {status}: {reason}")
        self.status = status
        self.reason = reason
        self.body = body


# ---------------------------------------------------------------------------
# Core client class
# ---------------------------------------------------------------------------


class SugarCubeClient:
    """
    Client for a single SugarCube device.

    Authentication uses a 4-digit PIN to obtain a session cookie ('scauth')
    that is stored in a CookieJar and reused automatically.

    Example:
        sc = SugarCubeClient("http://192.168.1.50")
        sc.pair("1234")
        print(sc.get_audio_status())
        sc.set_click_repair(enabled=True, sensitivity=0.5)
    """

    def __init__(self, base_url: str, timeout: int = 10):
        """
        Args:
            base_url: Base URL of the SugarCube, e.g. "http://192.168.1.50".
                      If no port is specified, port 5123 is used by default.
            timeout:  HTTP request timeout in seconds.
        """
        parsed = urlparse(base_url)
        if not parsed.port:
            netloc = f"{parsed.hostname}:5123"
            parsed = parsed._replace(netloc=netloc)
        self.base_url = urlunparse(parsed).rstrip("/")
        self.timeout = timeout

        # Cookie jar replaces requests.Session cookie management
        self._jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._jar))
        self._opener.addheaders = [("User-Agent", "SugarCubePythonClient/1.0")]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(self, req: Request) -> dict:
        """Execute a request, raise HTTPStatusError on non-2xx, return parsed JSON."""
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
                if resp.status < 200 or resp.status >= 300:
                    raise HTTPStatusError(resp.status, resp.reason, body)
                try:
                    return json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {}
        except HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            raise HTTPStatusError(e.code, e.reason, body) from e

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self._url(path)
        if params:
            url = f"{url}?{urlencode(params)}"
        req = Request(url, method="GET")
        return self._request(req)

    def _post(
        self,
        path: str,
        data: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        url = self._url(path)
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            req = Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            encoded = urlencode(data or {}).encode("utf-8")
            req = Request(
                url,
                data=encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
        return self._request(req)

    def _set_cookie(self, value: str):
        """
        Set the scauth cookie, replacing any existing instances.
        We clear matching cookies from the jar manually to avoid duplicates,
        then inject a new one via a synthetic Set-Cookie response.
        """
        # Remove all existing scauth cookies from the jar
        cookies_to_remove = [c for c in self._jar if c.name == "scauth"]
        for c in cookies_to_remove:
            self._jar.clear(c.domain, c.path, c.name)

        # Inject a new cookie by making a MockResponse the cookiejar will accept
        parsed = urlparse(self.base_url)
        domain = parsed.hostname or "localhost"

        import http.cookiejar as _cj
        import urllib.response as _ur

        class _MockResponse:
            """Minimal urllib response duck-type for CookieJar.extract_cookies."""
            def __init__(self, headers):
                self._headers = headers

            def info(self):
                return self

            def get_all(self, name, default=None):
                return [v for k, v in self._headers if k.lower() == name.lower()] or default

        mock_req = Request(self.base_url)
        mock_resp = _MockResponse([("Set-Cookie", f"scauth={value}; Path=/")])
        self._jar.extract_cookies(mock_resp, mock_req)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Authentication / Pairing
    # ------------------------------------------------------------------

    def pair(self, pin: str) -> bool:
        """
        Authenticate with a 4-digit PIN displayed on the SugarCube.
        Stores the returned 'scauth' cookie for subsequent calls.

        Args:
            pin: 4-digit pairing code shown on the device.

        Returns:
            True on success, False if the PIN was rejected.
        """
        if not (pin.isdigit() and len(pin) == 4):
            raise ValueError("PIN must be exactly 4 digits.")
        try:
            data = self._post(
                "/api/v1/pair",
                data={"code": pin, "desc": "SugarCubePythonClient"},
            )
            if "scauth" in data:
                self._set_cookie(data["scauth"])
            return True
        except HTTPStatusError as e:
            if e.status in (401, 403):
                return False
            raise

    def try_auto_pair(self) -> bool:
        """
        Attempt automatic pairing (works if the device is configured to allow it).
        Returns True on success.
        """
        try:
            data = self._post(
                "/api/v1/pair",
                data={"code": "auto", "desc": "SugarCubePythonClient"},
            )
            if "scauth" in data:
                self._set_cookie(data["scauth"])
            return True
        except HTTPStatusError:
            return False

    def load_cookie(self, scauth_value: str):
        """
        Restore a previously saved scauth cookie so you don't need to re-pair.

        Args:
            scauth_value: The raw value of the 'scauth' cookie.
        """
        self._set_cookie(scauth_value)

    def get_cookie(self) -> Optional[str]:
        """Return the current scauth cookie value (save it to avoid re-pairing)."""
        for cookie in self._jar:
            if cookie.name == "scauth":
                return cookie.value
        return None

    # ------------------------------------------------------------------
    # Status / Monitoring
    # ------------------------------------------------------------------

    def get_audio_status(self) -> dict:
        """
        Fetch the current audio system status.

        Key fields returned:
            audio_route     : "processed" | "bypass" | "bridging"
            audio           : "SOUND_IN" | "SOUND_OUT" | "NOISE"
            i2srouting      : int (6=SugarCubeOnly, 3=RepairRecord, 4=RepairPlayback, 0=ExternalOnly)
            sensitivity     : float  - click repair sensitivity
            sensitivity_min : float
            sensitivity_max : float
            last_dnlevel    : float  - denoise level
            dnstop          : int    - 1 if denoise is active
            dnout           : "SOUND_OUT" | "SOUND_IN" | "NOISE"
            headphone_volume: int
            headphone_mute  : bool
            gain_input      : float
            gain_output     : float
            pitch_protect   : bool
            recording_state : "idle" | "recording" | "playback"
            xmosdata        : int    - encodes sample rate (bits 1+) and bit depth (bit 0)
            model           : int    - device model number
        """
        return self._get("/api/v1/audiosystemstatus", params={"format": "html"})

    def get_recording_status(self) -> dict:
        """
        Fetch the current recording state.

        Key fields returned:
            state       : "idle" | "recording" | "playback"
            routing     : int (i2srouting value)
            audio_route : str
        """
        return self._get("/api/v1/recordingstatus", params={"format": "html"})

    def get_recordings(self) -> dict:
        """Return a list of stored recordings."""
        return self._get("/api/v1/recordings")

    def get_storage_status(self) -> dict:
        """Return USB/storage status (state, space, etc.)."""
        return self._get("/api/v1/storagestatus", params={"format": "html"})

    def get_playback_status(self) -> dict:
        """Return current playback state."""
        return self._get("/api/v1/playbackstatus", params={"format": "html"})

    def get_wifi_status(self) -> dict:
        """Return WiFi connection status."""
        return self._get("/api/v1/wifistatus", params={"format": "html"})

    def get_audio_levels(self) -> dict:
        """
        Return current audio levels / tuning data.

        Key fields:
            device_html, levels_html, confidence_html, tuning_html, mode
        """
        return self._get("/api/v1/audiolevels", params={"format": "html"})

    def get_clipping(self) -> dict:
        """Return current clipping indicator state."""
        return self._get("/api/v1/clipping", params={"format": "html"})

    def get_settings(self) -> dict:
        """Return device settings."""
        return self._get("/api/v1/settings")

    def check_eq_on(self) -> bool:
        """Return True if EQ is currently enabled."""
        data = self._get("/api/v1/checkeqon")
        return data.get("eq_on") == "true"

    # ------------------------------------------------------------------
    # Click Repair Controls
    # ------------------------------------------------------------------

    def set_click_repair(
        self,
        enabled: Optional[bool] = None,
        sensitivity: Optional[float] = None,
    ):
        """
        Enable/disable click repair and/or adjust sensitivity.

        When repair is ON,  audio="SOUND_OUT".
        When repair is OFF, audio="SOUND_IN".

        Args:
            enabled    : True to turn repair on, False to turn off. None = no change.
            sensitivity: Click repair sensitivity value (check get_audio_status for min/max).
        """
        if enabled is not None:
            self._get(
                "/api/v1/audiosystemchange",
                params={"audio": "SOUND_OUT" if enabled else "SOUND_IN"},
            )
        if sensitivity is not None:
            self._get(
                "/api/v1/audiosystemchange",
                params={"sensitivity": sensitivity},
            )

    def set_noise_reduction(
        self, enabled: Optional[bool] = None, level: Optional[float] = None
    ):
        """
        Enable/disable noise reduction (denoise) and/or adjust level.

        Args:
            enabled: True to engage noise reduction output, False to disable.
            level  : Denoise level (dnlevel).
        """
        if enabled is not None:
            self._get(
                "/api/v1/audiosystemchange",
                params={"dnout": "SOUND_OUT" if enabled else "SOUND_IN"},
            )
        if level is not None:
            self._get("/api/v1/audiosystemchange", params={"dnlevel": level})

    def sample_noise(self, start: bool = True):
        """
        Start or stop noise sampling (used to calibrate noise reduction).

        Args:
            start: True to start sampling, False to stop.
        """
        self._get(
            "/api/v1/sampling_audio_silence",
            params={"data": 1 if start else 0},
        )

    def stop_noise_reduction(self):
        """Disengage noise reduction."""
        self._get("/api/v1/audiosystemchange", params={"dnstop": 1})

    def set_eq(self, enabled: bool, eq_value: Optional[str] = None):
        """
        Enable or disable EQ.

        Args:
            enabled : True to enable EQ, False to disable.
            eq_value: EQ preset value (dneq) when enabling.
        """
        params = {"eq_on": "true" if enabled else "false"}
        if eq_value is not None:
            params["last_dneq"] = eq_value
        self._get("/api/v1/audiosystemchange", params=params)

    def set_audio_route(self, route: str):
        """
        Set the audio routing mode.

        Args:
            route: "processed" | "bypass" | "bridging"
        """
        if route not in ("processed", "bypass", "bridging"):
            raise ValueError("route must be 'processed', 'bypass', or 'bridging'.")
        self._get("/api/v1/audiosystemchange", params={"audio_route": route})

    def set_i2s_routing(self, routing: int):
        """
        Set the I2S routing gate (hardware routing).

        Args:
            routing: 6=SugarCubeOnly, 3=RepairRecord, 4=RepairPlayback, 0=ExternalOnly
        """
        self._get("/api/v1/audiosystemchange", params={"i2srouting": routing})

    def set_headphone_volume(self, volume: int):
        """Set headphone amplifier volume."""
        self._get("/api/v1/audiosystemchange", params={"headphone_volume": volume})

    def set_headphone_mute(self, muted: bool):
        """Mute or unmute the headphone output."""
        self._get("/api/v1/audiosystemchange", params={"headphone_mute": muted})

    def set_gain(
        self,
        gain_input: Optional[float] = None,
        gain_output: Optional[float] = None,
    ):
        """Set input and/or output gain."""
        params = {}
        if gain_input is not None:
            params["gain_input"] = gain_input
        if gain_output is not None:
            params["gain_output"] = gain_output
        if params:
            self._get("/api/v1/audiosystemchange", params=params)

    def clear_clipping(self):
        """Clear the clipping indicator."""
        self._post("/api/v1/clippingchange", data={"action": "clear"})

    # ------------------------------------------------------------------
    # VU Meter
    # ------------------------------------------------------------------

    def show_vu_meter(self):
        """Enable the VU meter."""
        self._get("/api/v1/show_vu")

    def hide_vu_meter(self):
        """Disable the VU meter."""
        self._get("/api/v1/stop_vu")

    # ------------------------------------------------------------------
    # Recording Controls
    # ------------------------------------------------------------------

    def start_recording(self) -> dict:
        """Start a new recording."""
        return self._get(
            "/api/v1/recordingchange",
            params={"record": "true", "hide": "false"},
        )

    def stop_recording(self) -> dict:
        """Stop the current recording."""
        return self._get(
            "/api/v1/recordingchange",
            params={"record": "false", "hide": "true"},
        )

    def delete_recording(self, recording_id: int):
        """Delete a specific recording by ID."""
        self._get("/api/v1/removebyid", params={"id": recording_id})

    def delete_all_recordings(self):
        """Delete all stored recordings. Use with caution."""
        self._get("/api/v1/delete_all_recordings")

    # ------------------------------------------------------------------
    # System / Misc
    # ------------------------------------------------------------------

    def send_command(self, command: str):
        """Send a hardware command (e.g. "identify")."""
        self._post("/api/v1/command", data={"command": command})

    def identify(self):
        """Flash the device LED to identify which unit this is."""
        self.send_command("identify")

    def check_for_updates(self):
        """Trigger an update check on the device."""
        self._get("/api/v1/updatecheck")

    def set_system_settings(self, **kwargs):
        """
        Update system settings. Pass keyword arguments matching setting names.

        Known settings:
            rec_stop          : int  - auto-stop recording after N minutes (1-45)
            rec_end_silence   : int  - silence detection timeout in minutes (1-10)
            display1_dur      : int  - primary display duration in seconds (5-600)
            display2_dur      : int  - secondary display duration in seconds (5-600)
            vu_offset         : float
            vu_scale          : float
            check_recsilence  : bool - enable silence-based recording stop
            check_display2dur : bool - enable secondary display duration
        """
        self._get("/api/v1/systemsettings", params=kwargs)

    def wifi_survey(self) -> dict:
        """Return a list of available WiFi networks."""
        return self._get("/api/v1/wifisurvey", params={"format": "html"})

    def connect_wifi(self, ssid: str, password: str):
        """Connect to a WiFi network."""
        self._post("/api/v1/wificonnect", data={"ssid": ssid, "password": password})


# ---------------------------------------------------------------------------
# Multi-device manager
# ---------------------------------------------------------------------------


class SugarCubeManager:
    """
    Manages connections to multiple SugarCube devices.

    Example:
        mgr = SugarCubeManager()
        mgr.add("living_room", "http://192.168.1.50")
        mgr.add("studio",      "http://192.168.1.51")

        mgr["living_room"].pair("1234")
        mgr["studio"].pair("5678")

        for name, sc in mgr.items():
            print(name, sc.get_recording_status())
    """

    def __init__(self):
        self._devices: dict[str, SugarCubeClient] = {}

    def add(self, name: str, base_url: str, timeout: int = 10) -> SugarCubeClient:
        client = SugarCubeClient(base_url, timeout=timeout)
        self._devices[name] = client
        return client

    def items(self):
        return self._devices.items()

    def names(self):
        return list(self._devices.keys())

    def __getitem__(self, name: str) -> SugarCubeClient:
        return self._devices[name]

    def __iter__(self):
        return iter(self._devices)


# ---------------------------------------------------------------------------
# Status helpers / data formatting
# ---------------------------------------------------------------------------

SR_LABELS = {
    0: "44.1 kHz",
    1: "48 kHz",
    2: "88.2 kHz",
    3: "96 kHz",
    4: "176.4 kHz",
    5: "192 kHz",
}
BIT_LABELS = {0: "16-bit", 1: "24-bit"}
ROUTE_MAP = {
    "processed": "Processed (SugarCube active)",
    "bypass": "Bypass (analog passthrough)",
    "bridging": "Bridging",
}
I2S_MAP = {
    6: "SugarCube Only",
    3: "Repair+Record",
    4: "Repair+Playback",
    0: "External Only",
}
AUDIO_MAP = {
    "SOUND_OUT": "Repair ON",
    "SOUND_IN": "Repair OFF / monitoring",
    "NOISE": "Noise monitor",
}


def decode_status(audio: dict) -> dict:
    """Pull the key display fields out of a raw audio status response."""
    sr_raw = audio.get("xmosdata", 0)
    sr_idx = sr_raw >> 1
    bit_idx = sr_raw & 0b1
    return {
        "audio_route": ROUTE_MAP.get(
            audio.get("audio_route", ""), audio.get("audio_route", "?")
        ),
        "i2s_routing": I2S_MAP.get(
            audio.get("i2srouting"), audio.get("i2srouting", "?")
        ),
        "repair_mode": AUDIO_MAP.get(
            audio.get("audio"), audio.get("audio", "?")
        ),
        "sensitivity": audio.get("sensitivity", "?"),
        "sens_min": audio.get("sensitivity_min", "?"),
        "sens_max": audio.get("sensitivity_max", "?"),
        "denoise_active": "Yes" if audio.get("dnstop") == 1 else "No",
        "denoise_level": audio.get("last_dnlevel", "?"),
        "eq": "On" if audio.get("last_dneq") else "Off/unset",
        "hp_volume": audio.get("headphone_volume", "?"),
        "hp_mute": "Yes" if audio.get("headphone_mute") else "No",
        "gain_in": audio.get("gain_input", "?"),
        "gain_out": audio.get("gain_output", "?"),
        "sample_rate": SR_LABELS.get(sr_idx, str(sr_idx)),
        "bit_depth": BIT_LABELS.get(bit_idx, str(bit_idx)),
        "rec_state": audio.get("recording_state", "?"),
        "model": audio.get("model", "?"),
    }


def print_status(name: str, sc: SugarCubeClient):
    """Print a human-readable status summary for a device."""
    print(f"\n{'='*60}")
    print(f"  Device : {name}  --  {sc.base_url}")
    print(f"{'='*60}")

    try:
        audio = sc.get_audio_status()
    except HTTPStatusError as e:
        print(f"  ERROR fetching audio status: {e}")
        return

    s = decode_status(audio)
    print(f"  Audio route    : {s['audio_route']}")
    print(f"  I2S routing    : {s['i2s_routing']}")
    print(f"  Repair mode    : {s['repair_mode']}")
    print(f"  Click sens.    : {s['sensitivity']}  (range {s['sens_min']} - {s['sens_max']})")
    print(f"  Denoise active : {s['denoise_active']}")
    print(f"  Denoise level  : {s['denoise_level']}")
    print(f"  EQ             : {s['eq']}")
    print(f"  Headphone vol  : {s['hp_volume']}")
    print(f"  Headphone mute : {s['hp_mute']}")
    print(f"  Gain in/out    : {s['gain_in']} / {s['gain_out']}")
    print(f"  Sample rate    : {s['sample_rate']}")
    print(f"  Bit depth      : {s['bit_depth']}")
    print(f"  Recording state: {s['rec_state']}")
    print(f"  Model          : {s['model']}")


# ---------------------------------------------------------------------------
# Plain-text polling monitor
# ---------------------------------------------------------------------------


def run_monitor_plain(sc: SugarCubeClient, name: str, interval: int):
    """
    Poll the device every `interval` seconds and print a refreshed status block.
    Press Ctrl-C to stop.
    """
    print(
        f"Monitoring {name} ({sc.base_url})  --  refresh every {interval}s  --  Ctrl-C to stop\n"
    )
    try:
        while True:
            print_status(name, sc)
            print(f"\n  Last updated: {datetime.now().strftime('%H:%M:%S')}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


# ---------------------------------------------------------------------------
# Curses TUI monitor
# ---------------------------------------------------------------------------

# Colour pair indices
_C_TITLE = 1
_C_LABEL = 2
_C_VALUE = 3
_C_GOOD = 4
_C_WARN = 5
_C_ERROR = 6
_C_HEADER = 7


def _init_colours():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_C_TITLE, curses.COLOR_CYAN, -1)
    curses.init_pair(_C_LABEL, curses.COLOR_WHITE, -1)
    curses.init_pair(_C_VALUE, curses.COLOR_YELLOW, -1)
    curses.init_pair(_C_GOOD, curses.COLOR_GREEN, -1)
    curses.init_pair(_C_WARN, curses.COLOR_YELLOW, -1)
    curses.init_pair(_C_ERROR, curses.COLOR_RED, -1)
    curses.init_pair(_C_HEADER, curses.COLOR_BLACK, curses.COLOR_CYAN)


def _safe_addstr(win, row: int, col: int, text: str, attr=0):
    """Write text to a curses window, silently ignoring out-of-bounds errors."""
    max_y, max_x = win.getmaxyx()
    if row < 0 or row >= max_y or col < 0 or col >= max_x:
        return
    max_len = max_x - col
    if max_len <= 0:
        return
    try:
        win.addstr(row, col, text[:max_len], attr)
    except curses.error:
        pass


def _draw_tui(
    stdscr,
    name: str,
    sc: SugarCubeClient,
    interval: int,
    status: dict,
    error: Optional[str],
    last_updated: Optional[str],
):
    """Render a single frame of the TUI."""
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()

    # Header bar
    header = f"  SugarCube Monitor  |  {name}  |  {sc.base_url}  "
    _safe_addstr(
        stdscr,
        0,
        0,
        header.ljust(max_x),
        curses.color_pair(_C_HEADER) | curses.A_BOLD,
    )

    if error:
        _safe_addstr(
            stdscr, 2, 2, "ERROR:", curses.color_pair(_C_ERROR) | curses.A_BOLD
        )
        _safe_addstr(stdscr, 2, 9, error, curses.color_pair(_C_ERROR))
        _safe_addstr(
            stdscr,
            max_y - 1,
            2,
            f"Retrying every {interval}s  |  q = quit",
            curses.color_pair(_C_LABEL),
        )
        stdscr.refresh()
        return

    if not status:
        _safe_addstr(
            stdscr, 2, 2, "Waiting for data...", curses.color_pair(_C_WARN)
        )
        stdscr.refresh()
        return

    # Layout constants
    COL_L = 2
    COL_V = 22
    COL_R = max_x // 2 + 2
    COL_RV = COL_R + 20

    def lv(row, label, value, col_l=COL_L, col_v=COL_V, val_colour=_C_VALUE):
        _safe_addstr(
            stdscr,
            row,
            col_l,
            f"{label:<{col_v - col_l - 1}}",
            curses.color_pair(_C_LABEL),
        )
        _safe_addstr(stdscr, row, col_v, str(value), curses.color_pair(val_colour))

    def lv_right(row, label, value, val_colour=_C_VALUE):
        lv(row, label, value, col_l=COL_R, col_v=COL_RV, val_colour=val_colour)

    def section(row, title):
        _safe_addstr(
            stdscr,
            row,
            COL_L,
            f"-- {title} {'-' * max(0, max_x - COL_L - len(title) - 5)}",
            curses.color_pair(_C_TITLE),
        )

    # Audio section
    row = 2
    section(row, "Audio")
    row += 1

    repair_colour = _C_GOOD if "ON" in status["repair_mode"] else _C_WARN
    lv(row, "Route:", status["audio_route"])
    lv_right(row, "I2S routing:", status["i2s_routing"])
    row += 1

    lv(row, "Repair mode:", status["repair_mode"], val_colour=repair_colour)
    lv_right(row, "Model:", status["model"])
    row += 1

    lv(row, "Sample rate:", status["sample_rate"])
    lv_right(row, "Bit depth:", status["bit_depth"])
    row += 1

    # Click Repair section
    row += 1
    section(row, "Click Repair")
    row += 1
    lv(
        row,
        "Sensitivity:",
        f"{status['sensitivity']}  (range {status['sens_min']} - {status['sens_max']})",
    )
    row += 1

    # Noise Reduction section
    row += 1
    section(row, "Noise Reduction")
    row += 1
    dn_colour = _C_GOOD if status["denoise_active"] == "Yes" else _C_LABEL
    lv(row, "Active:", status["denoise_active"], val_colour=dn_colour)
    lv_right(row, "Level:", status["denoise_level"])
    row += 1
    lv(row, "EQ:", status["eq"])
    row += 1

    # Headphones section
    row += 1
    section(row, "Headphones")
    row += 1
    mute_colour = _C_WARN if status["hp_mute"] == "Yes" else _C_GOOD
    lv(row, "Volume:", status["hp_volume"])
    lv_right(row, "Muted:", status["hp_mute"], val_colour=mute_colour)
    row += 1
    lv(row, "Gain in:", status["gain_in"])
    lv_right(row, "Gain out:", status["gain_out"])
    row += 1

    # Recording section
    row += 1
    section(row, "Recording")
    row += 1
    rec_colour = _C_ERROR if status["rec_state"] == "recording" else _C_GOOD
    lv(row, "State:", status["rec_state"], val_colour=rec_colour)
    row += 1

    # Footer
    updated_str = f"Updated: {last_updated}" if last_updated else ""
    footer = f"  q = quit  |  refresh every {interval}s  |  {updated_str}"
    _safe_addstr(
        stdscr, max_y - 1, 0, footer.ljust(max_x), curses.color_pair(_C_HEADER)
    )

    stdscr.refresh()


def run_monitor_tui(sc: SugarCubeClient, name: str, interval: int):
    """
    Run the curses TUI monitor. A background thread polls the device;
    the main thread redraws the screen. Press 'q' to quit.
    """
    state = {
        "status": {},
        "error": None,
        "last_updated": None,
        "quit": False,
    }
    lock = threading.Lock()

    def poll():
        while not state["quit"]:
            try:
                audio = sc.get_audio_status()
                decoded = decode_status(audio)
                with lock:
                    state["status"] = decoded
                    state["error"] = None
                    state["last_updated"] = datetime.now().strftime("%H:%M:%S")
            except Exception as e:
                with lock:
                    state["error"] = str(e)
            # Sleep in small increments so we can respond to quit quickly
            for _ in range(interval * 10):
                if state["quit"]:
                    break
                time.sleep(0.1)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()

    def tui_main(stdscr):
        _init_colours()
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(200)

        while True:
            with lock:
                s = dict(state["status"])
                e = state["error"]
                lu = state["last_updated"]

            _draw_tui(stdscr, name, sc, interval, s, e, lu)

            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):  # q, Q, or Escape
                state["quit"] = True
                break

    try:
        curses.wrapper(tui_main)
    except KeyboardInterrupt:
        state["quit"] = True

    poller.join(timeout=2)
    print("Monitor stopped.")


# ---------------------------------------------------------------------------
# Authentication helper (shared by CLI commands)
# ---------------------------------------------------------------------------


def authenticate(sc: SugarCubeClient, args, config: dict, device_name: str):
    """
    Authenticate sc using: command-line cookie > config cookie > auto-pair > PIN.
    Saves any newly acquired cookie back to the config.
    """
    # 1. Explicit cookie on command line
    if hasattr(args, "cookie") and args.cookie:
        sc.load_cookie(args.cookie)
        return

    # 2. Cookie stored in config
    dev_cfg = config.get("devices", {}).get(device_name, {})
    if dev_cfg.get("cookie"):
        sc.load_cookie(dev_cfg["cookie"])
        return

    # 3. Auto-pair
    if sc.try_auto_pair():
        cookie = sc.get_cookie()
        if cookie:
            config_save_cookie(config, device_name, cookie)
        return

    # 4. PIN from args or config
    pin = getattr(args, "pin", None) or dev_cfg.get("pin")
    if pin:
        ok = sc.pair(str(pin))
        if not ok:
            print("ERROR: Pairing failed. Check your PIN.", file=sys.stderr)
            sys.exit(1)
        print("Paired successfully.")
        cookie = sc.get_cookie()
        if cookie:
            print(f"Cookie saved to {CONFIG_PATH}")
            config_save_cookie(config, device_name, cookie)
        return

    print(
        "ERROR: Authentication failed. Provide --pin or add a cookie/pin to the config.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SweetVinyl SugarCube command-line client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Config file ({CONFIG_PATH}):
  Store devices, PINs, and cookies so you don't need to repeat them on
  every invocation. Cookies are saved automatically after successful pairing.

  Example ~/.sugarcube.json:
  {{
      "devices": {{
          "living_room": {{
              "url": "http://192.168.1.50",
              "pin": "1234"
          }},
          "studio": {{
              "url": "http://192.168.1.51",
              "pin": "5678"
          }}
      }},
      "default_device": "living_room",
      "default_interval": 5,
      "timeout": 10
  }}

Examples:
  # Save a device to config, then use it by name
  python sugarcube_client.py config --add studio --url http://192.168.1.51 --pin 5678
  python sugarcube_client.py config --default studio
  python sugarcube_client.py status

  # One-off with explicit URL and PIN
  python sugarcube_client.py --url http://192.168.1.50 --pin 1234 status

  # Poll status every 5 seconds (plain text)
  python sugarcube_client.py monitor

  # Poll with curses TUI, refresh every 3 seconds
  python sugarcube_client.py monitor --interval 3 --tui

  # Turn click repair on / off
  python sugarcube_client.py repair --on
  python sugarcube_client.py repair --off --sensitivity 0.45

  # Enable noise reduction at level 0.3
  python sugarcube_client.py denoise --on --level 0.3

  # Start / stop recording
  python sugarcube_client.py record --start
  python sugarcube_client.py record --stop

  # Set headphone volume and unmute
  python sugarcube_client.py volume --set 80 --unmute

  # Flash the identify LED
  python sugarcube_client.py identify

  # List stored recordings
  python sugarcube_client.py recordings

  # List configured devices
  python sugarcube_client.py config --list
""",
    )

    p.add_argument(
        "--url",
        help="SugarCube base URL, e.g. http://192.168.1.50 (port 5123 default)",
    )
    p.add_argument("--device", help="Named device from config file")
    p.add_argument("--pin", help="4-digit pairing PIN")
    p.add_argument(
        "--cookie", help="Reuse a saved scauth cookie value to skip pairing"
    )
    p.add_argument(
        "--timeout", type=int, default=None, help="HTTP timeout in seconds"
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text",
    )

    sub = p.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Show full device status")

    # monitor
    mon = sub.add_parser("monitor", help="Poll device status continuously")
    mon.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Refresh interval in seconds (default: 5, or from config)",
    )
    mon.add_argument(
        "--tui",
        action="store_true",
        help="Use curses TUI instead of plain-text output",
    )

    # repair
    r = sub.add_parser("repair", help="Control click repair")
    rg = r.add_mutually_exclusive_group()
    rg.add_argument("--on", action="store_true", help="Enable click repair")
    rg.add_argument("--off", action="store_true", help="Disable click repair")
    r.add_argument("--sensitivity", type=float, help="Set repair sensitivity level")

    # denoise
    d = sub.add_parser("denoise", help="Control noise reduction")
    dg = d.add_mutually_exclusive_group()
    dg.add_argument("--on", action="store_true", help="Enable noise reduction")
    dg.add_argument("--off", action="store_true", help="Disable noise reduction")
    d.add_argument("--level", type=float, help="Set denoise level")
    d.add_argument(
        "--sample",
        action="store_true",
        help="Start noise sampling (alias: --learn)",
    )
    d.add_argument(
        "--learn",
        action="store_true",
        help="Start noise learning (alias: --sample)",
    )
    d.add_argument(
        "--stop-sample",
        action="store_true",
        help="Stop noise sampling/learning (alias: --stop-learn)",
    )
    d.add_argument(
        "--stop-learn",
        action="store_true",
        help="Stop noise learning (alias: --stop-sample)",
    )

    # record
    rec = sub.add_parser("record", help="Control recording")
    recg = rec.add_mutually_exclusive_group(required=True)
    recg.add_argument("--start", action="store_true", help="Start recording")
    recg.add_argument("--stop", action="store_true", help="Stop recording")

    # recordings
    sub.add_parser("recordings", help="List stored recordings")

    # volume
    vol = sub.add_parser("volume", help="Control headphone volume")
    vol.add_argument("--set", type=int, metavar="LEVEL", help="Set volume level")
    vol.add_argument("--mute", action="store_true", help="Mute headphones")
    vol.add_argument("--unmute", action="store_true", help="Unmute headphones")

    # route
    rt = sub.add_parser("route", help="Set audio routing mode")
    rt.add_argument("mode", choices=["processed", "bypass", "bridging"])

    # eq
    eq = sub.add_parser("eq", help="Enable or disable EQ")
    eqg = eq.add_mutually_exclusive_group(required=True)
    eqg.add_argument("--on", action="store_true")
    eqg.add_argument("--off", action="store_true")
    eq.add_argument("--preset", help="EQ preset value")

    # identify
    sub.add_parser("identify", help="Flash the device LED")

    # clipping
    sub.add_parser("clipping", help="Show and clear the clipping indicator")

    # wifi
    sub.add_parser("wifi", help="Show WiFi status and available networks")

    # config
    cfg = sub.add_parser("config", help="Manage the config file")
    cfg_grp = cfg.add_mutually_exclusive_group(required=True)
    cfg_grp.add_argument(
        "--list", action="store_true", help="List configured devices"
    )
    cfg_grp.add_argument(
        "--add", metavar="NAME", help="Add or update a device by name"
    )
    cfg_grp.add_argument(
        "--remove", metavar="NAME", help="Remove a device by name"
    )
    cfg_grp.add_argument(
        "--default", metavar="NAME", help="Set the default device"
    )
    cfg.add_argument("--url", dest="cfg_url", help="URL for --add")
    cfg.add_argument("--pin", dest="cfg_pin", help="PIN for --add")

    return p


def resolve_device(args, config: dict) -> tuple[str, str]:
    """
    Work out the device URL and friendly name from args + config.
    Returns (url, name).
    """
    devices = config.get("devices", {})

    if args.url:
        return args.url, args.url

    name = getattr(args, "device", None) or config.get("default_device")

    if name and name in devices:
        url = devices[name].get("url")
        if url:
            return url, name

    print(
        "ERROR: No device URL specified and no default_device in config.\n"
        f"Use --url http://... or add a device with:\n"
        f"  {sys.argv[0]} config --add NAME --url URL --pin PIN\n"
        f"  {sys.argv[0]} config --default NAME",
        file=sys.stderr,
    )
    sys.exit(1)


def main():
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()
    cmd = args.command

    # Config management — no device connection needed
    if cmd == "config":
        if args.list:
            devices = config.get("devices", {})
            default = config.get("default_device", "(none)")
            if not devices:
                print("No devices configured.")
            else:
                print(f"\n{'NAME':<20} {'URL':<35} {'PIN':<8} COOKIE")
                print("-" * 75)
                for dname, dcfg in devices.items():
                    marker = " *" if dname == default else ""
                    cookie = "saved" if dcfg.get("cookie") else "-"
                    print(
                        f"{dname + marker:<20} {dcfg.get('url','?'):<35} "
                        f"{dcfg.get('pin','?'):<8} {cookie}"
                    )
                print(f"\n  * = default device")
        elif args.add:
            if not args.cfg_url:
                print("ERROR: --url is required with --add", file=sys.stderr)
                sys.exit(1)
            config.setdefault("devices", {})[args.add] = {
                k: v
                for k, v in {"url": args.cfg_url, "pin": args.cfg_pin}.items()
                if v is not None
            }
            save_config(config)
            print(f"Device '{args.add}' saved.")
        elif args.remove:
            if args.remove in config.get("devices", {}):
                del config["devices"][args.remove]
                save_config(config)
                print(f"Device '{args.remove}' removed.")
            else:
                print(f"Device '{args.remove}' not found in config.")
        elif args.default:
            if args.default not in config.get("devices", {}):
                print(
                    f"ERROR: '{args.default}' is not in the config. Add it first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            config["default_device"] = args.default
            save_config(config)
            print(f"Default device set to '{args.default}'.")
        return

    # All other commands need a device connection
    url, device_name = resolve_device(args, config)
    timeout = getattr(args, "timeout", None) or config.get("timeout", 10)
    sc = SugarCubeClient(url, timeout=timeout)
    authenticate(sc, args, config, device_name)

    # Status
    if cmd == "status":
        if args.json:
            print(json.dumps(sc.get_audio_status(), indent=2))
        else:
            print_status(device_name, sc)

    # Monitor
    elif cmd == "monitor":
        interval = args.interval or config.get("default_interval", 5)
        if args.tui:
            run_monitor_tui(sc, device_name, interval)
        else:
            run_monitor_plain(sc, device_name, interval)

    # Click Repair
    elif cmd == "repair":
        enabled = True if args.on else (False if args.off else None)
        sc.set_click_repair(enabled=enabled, sensitivity=args.sensitivity)
        if enabled is True:
            print("Click repair: ON")
        elif enabled is False:
            print("Click repair: OFF")
        if args.sensitivity is not None:
            print(f"Sensitivity set to: {args.sensitivity}")

    # Denoise
    elif cmd == "denoise":
        if args.sample or args.learn:
            sc.sample_noise(start=True)
            print("Noise learning started.")
        elif args.stop_sample or args.stop_learn:
            sc.sample_noise(start=False)
            print("Noise learning stopped.")
        else:
            enabled = True if args.on else (False if args.off else None)
            sc.set_noise_reduction(enabled=enabled, level=args.level)
            if enabled is True:
                print("Noise reduction: ON")
            elif enabled is False:
                print("Noise reduction: OFF")
            if args.level is not None:
                print(f"Denoise level set to: {args.level}")

    # Recording
    elif cmd == "record":
        if args.start:
            result = sc.start_recording()
            print("Recording started.")
            if args.json:
                print(json.dumps(result, indent=2))
        elif args.stop:
            result = sc.stop_recording()
            print("Recording stopped.")
            if args.json:
                print(json.dumps(result, indent=2))

    # Recordings list
    elif cmd == "recordings":
        data = sc.get_recordings()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            recordings = data.get("recordings", [])
            if not recordings:
                print("No recordings found.")
            else:
                print(f"\n{'ID':<8} {'State':<12} {'Title'}")
                print("-" * 50)
                for rec in recordings:
                    print(
                        f"{rec.get('id','?'):<8} {rec.get('state','?'):<12} "
                        f"{rec.get('title', '(untitled)')}"
                    )

    # Volume
    elif cmd == "volume":
        if args.set is not None:
            sc.set_headphone_volume(args.set)
            print(f"Headphone volume set to: {args.set}")
        if args.mute:
            sc.set_headphone_mute(True)
            print("Headphones muted.")
        if args.unmute:
            sc.set_headphone_mute(False)
            print("Headphones unmuted.")

    # Route
    elif cmd == "route":
        sc.set_audio_route(args.mode)
        print(f"Audio route set to: {args.mode}")

    # EQ
    elif cmd == "eq":
        sc.set_eq(enabled=args.on, eq_value=args.preset)
        print(f"EQ: {'ON' if args.on else 'OFF'}")

    # Identify
    elif cmd == "identify":
        sc.identify()
        print("Identify command sent (device LED should flash).")

    # Clipping
    elif cmd == "clipping":
        data = sc.get_clipping()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(
                f"Clipping indicator: {'active' if data.get('html') else 'clear'}"
            )
            sc.clear_clipping()
            print("Clipping indicator cleared.")

    # WiFi
    elif cmd == "wifi":
        status = sc.get_wifi_status()
        survey = sc.wifi_survey()
        if args.json:
            print(json.dumps({"status": status, "survey": survey}, indent=2))
        else:
            print(f"WiFi status: {status}")
            print(f"Available networks: {survey}")


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:\t%(message)s",
        level=logging.INFO,
    )
    main()
