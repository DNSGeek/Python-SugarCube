# SugarCube Python Client

A command-line client and Python library for controlling [SweetVinyl SugarCube](https://sweetvinyl.com/sugarcube/) vinyl restoration devices over their local HTTP API.

## Features

- **Full device control** — click repair, noise reduction, EQ, audio routing, headphone volume and gain
- **Recording control** — start, stop, and list recordings
- **Live monitoring** — plain-text polling monitor or a colour-coded curses TUI
- **Multi-device support** — manage any number of devices by name from a single config file
- **Persistent config** — devices, PINs, and session cookies stored in `~/.sugarcube.json`
- **Python API** — import `SugarCubeClient` directly into your own scripts

---

## Requirements

* **sugarcube_client.py**: None, Any Python 3.9+ is all you need.
* **sugarcube_menubar.py**: Uses the [Python rumps](https://github.com/jaredks/rumps) package.

---

## Quick Start

```bash
# Add your device to the config
python sugarcube_client.py config --add living_room --url http://10.10.0.168 --pin 1111

# Set it as the default so you don't need to specify it every time
python sugarcube_client.py config --default living_room

# Show device status
python sugarcube_client.py status
```

On first use the script pairs with the device using your PIN and saves the session cookie automatically. Subsequent invocations use the saved cookie and don't need the PIN.

---

## Config File

The config file lives at `~/.sugarcube.json` and is shared with the menu bar app. You can edit it by hand or manage it entirely through the `config` subcommand.

```json
{
    "devices": {
        "living_room": {
            "url": "http://10.10.0.168",
            "pin": "1111",
            "cookie": "auto-saved-after-first-pair"
        },
        "studio": {
            "url": "http://10.10.0.169",
            "pin": "2222"
        }
    },
    "default_device": "living_room",
    "default_interval": 5,
    "timeout": 10
}
```

| Key | Description |
|-----|-------------|
| `url` | Device IP address. Port 5123 is added automatically if omitted. |
| `pin` | 4-digit pairing PIN shown on the device display. Only needed once — the cookie is saved after successful pairing. |
| `cookie` | Session cookie saved automatically after pairing. Do not edit this by hand. |
| `default_device` | Device used when `--url` and `--device` are both omitted. |
| `default_interval` | Default polling interval in seconds for the `monitor` command. |
| `timeout` | HTTP request timeout in seconds (default: 10). |

---

## Global Options

These options apply to all commands except `config`:

| Option | Description |
|--------|-------------|
| `--url URL` | Device URL, e.g. `http://10.10.0.168`. Overrides config. |
| `--device NAME` | Named device from the config file. |
| `--pin PIN` | 4-digit pairing PIN (only needed if not already in config). |
| `--cookie VALUE` | Raw `scauth` cookie value to bypass pairing entirely. |
| `--timeout N` | HTTP timeout in seconds. Overrides config. |
| `--json` | Print raw JSON output instead of formatted text. |

---

## Commands

### `status`

Show a full summary of the device's current state.

```bash
python sugarcube_client.py status
python sugarcube_client.py --device studio status
python sugarcube_client.py --url http://10.10.0.168 status --json
```

Output includes audio route, I2S routing, click repair state and sensitivity, noise reduction state and level, EQ, headphone volume and mute, gain, sample rate, bit depth, recording state, and model number.

---

### `monitor`

Poll the device continuously and display live status. Press `Ctrl-C` to stop.

```bash
# Plain-text output, default interval
python sugarcube_client.py monitor

# Plain-text, custom interval
python sugarcube_client.py monitor --interval 3

# Curses TUI (colour-coded, press q to quit)
python sugarcube_client.py monitor --tui
python sugarcube_client.py monitor --interval 3 --tui
```

| Option | Description |
|--------|-------------|
| `--interval N` | Refresh interval in seconds. Defaults to `default_interval` in config, or 5. |
| `--tui` | Launch the curses TUI instead of plain-text output. |

The TUI displays all status fields in a two-column layout with colour coding: green for active/healthy states, yellow for warnings, red for errors or active recording. A background thread handles polling so the UI never blocks.

---

### `repair`

Control click repair on/off and adjust sensitivity (1–10).

```bash
# Enable or disable
python sugarcube_client.py repair --on
python sugarcube_client.py repair --off

# Set sensitivity level (1–10)
python sugarcube_client.py repair --sensitivity 5

# Combine: enable and set level in one call
python sugarcube_client.py repair --on --sensitivity 7
```

| Option | Description |
|--------|-------------|
| `--on` | Enable click repair. |
| `--off` | Disable click repair. |
| `--sensitivity N` | Set repair sensitivity (1–10). |

---

### `denoise`

Control noise reduction on/off, adjust level, and manage noise learning.

```bash
# Enable or disable
python sugarcube_client.py denoise --on
python sugarcube_client.py denoise --off

# Set denoise level (1–10)
python sugarcube_client.py denoise --level 8

# Combine: enable and set level
python sugarcube_client.py denoise --on --level 6

# Noise learning — play a silent section of the record first
python sugarcube_client.py denoise --learn
python sugarcube_client.py denoise --stop-learn
```

| Option | Description |
|--------|-------------|
| `--on` | Enable noise reduction. |
| `--off` | Disable noise reduction. |
| `--level N` | Set denoise level (1–10). |
| `--learn` | Start noise sampling. Play a silent groove to let the device learn the noise profile. Alias: `--sample`. |
| `--stop-learn` | Stop noise sampling. Alias: `--stop-sample`. |

---

### `record`

Start or stop recording to USB storage.

```bash
python sugarcube_client.py record --start
python sugarcube_client.py record --stop
```

---

### `recordings`

List all recordings currently stored on the device.

```bash
python sugarcube_client.py recordings
python sugarcube_client.py recordings --json
```

---

### `volume`

Control the headphone output.

```bash
# Set volume level
python sugarcube_client.py volume --set 75

# Mute / unmute
python sugarcube_client.py volume --mute
python sugarcube_client.py volume --unmute

# Combine: set level and unmute
python sugarcube_client.py volume --set 75 --unmute
```

| Option | Description |
|--------|-------------|
| `--set N` | Set headphone volume level. |
| `--mute` | Mute headphone output. |
| `--unmute` | Unmute headphone output. |

---

### `route`

Set the audio routing mode.

```bash
python sugarcube_client.py route processed
python sugarcube_client.py route bypass
python sugarcube_client.py route bridging
```

| Mode | Description |
|------|-------------|
| `processed` | Audio passes through the SugarCube DSP. |
| `bypass` | Analog passthrough, SugarCube bypassed. |
| `bridging` | Bridging mode. |

---

### `eq`

Enable or disable the EQ.

```bash
python sugarcube_client.py eq --on
python sugarcube_client.py eq --off
python sugarcube_client.py eq --on --preset flat
```

| Option | Description |
|--------|-------------|
| `--on` | Enable EQ. |
| `--off` | Disable EQ. |
| `--preset VALUE` | EQ preset value to apply when enabling. |

---

### `clipping`

Show the clipping indicator state and clear it.

```bash
python sugarcube_client.py clipping
```

---

### `identify`

Flash the device LED to identify which physical unit you are connected to. Useful when you have multiple SugarCubes on the same network.

```bash
python sugarcube_client.py identify
```

---

### `wifi`

Show WiFi connection status and scan for available networks.

```bash
python sugarcube_client.py wifi
python sugarcube_client.py wifi --json
```

---

### `config`

Manage the `~/.sugarcube.json` config file.

```bash
# List all configured devices (* marks the default)
python sugarcube_client.py config --list

# Add or update a device
python sugarcube_client.py config --add living_room --url http://10.10.0.168 --pin 1111

# Set the default device
python sugarcube_client.py config --default living_room

# Remove a device
python sugarcube_client.py config --remove studio
```

| Option | Description |
|--------|-------------|
| `--list` | Print all configured devices. |
| `--add NAME` | Add or update a device. Requires `--url`; `--pin` is optional. |
| `--remove NAME` | Remove a device from the config. |
| `--default NAME` | Set the named device as the default. |

---

## Python API

`SugarCubeClient` can be imported and used directly in your own scripts.

### Basic usage

```python
from sugarcube_client import SugarCubeClient

sc = SugarCubeClient("http://10.10.0.168")

# Pair with a PIN (only needed once)
sc.pair("1111")

# Save the cookie so you can restore it next time
cookie = sc.get_cookie()

# On subsequent runs, restore the cookie instead of re-pairing
sc.load_cookie(cookie)
```

### Authentication methods

| Method | Description |
|--------|-------------|
| `pair(pin)` | Pair with a 4-digit PIN. Returns `True` on success, `False` if PIN rejected. |
| `try_auto_pair()` | Attempt automatic pairing without a PIN. Returns `True` on success. |
| `load_cookie(value)` | Restore a previously saved `scauth` cookie. |
| `get_cookie()` | Return the current `scauth` cookie value for saving. |

### Status queries

```python
audio   = sc.get_audio_status()     # Full audio system status
rec     = sc.get_recording_status() # Recording state
clips   = sc.get_recordings()       # List of stored recordings
storage = sc.get_storage_status()   # USB storage info
wifi    = sc.get_wifi_status()      # WiFi connection status
clip    = sc.get_clipping()         # Clipping indicator state
eq_on   = sc.check_eq_on()          # True if EQ is enabled
```

### Click repair

```python
sc.set_click_repair(enabled=True)           # Turn on
sc.set_click_repair(enabled=False)          # Turn off
sc.set_click_repair(sensitivity=7)          # Set level (1–10)
sc.set_click_repair(enabled=True, sensitivity=5)  # Both at once
```

### Noise reduction

```python
sc.set_noise_reduction(enabled=True)        # Turn on
sc.set_noise_reduction(enabled=False)       # Turn off
sc.set_noise_reduction(level=8)             # Set level (1–10)
sc.sample_noise(start=True)                 # Start noise learning
sc.sample_noise(start=False)                # Stop noise learning
sc.stop_noise_reduction()                   # Disengage NR output
```

### Audio routing and levels

```python
sc.set_audio_route("processed")             # "processed" | "bypass" | "bridging"
sc.set_i2s_routing(6)                       # 6=SugarCubeOnly, 3=RepairRecord,
                                            # 4=RepairPlayback, 0=ExternalOnly
sc.set_headphone_volume(75)
sc.set_headphone_mute(True)
sc.set_gain(gain_input=-6.0, gain_output=1.0)
sc.set_eq(enabled=True, eq_value="flat")
sc.clear_clipping()
```

### Recording

```python
sc.start_recording()
sc.stop_recording()
sc.delete_recording(recording_id=3)
sc.delete_all_recordings()                  # Use with caution
```

### System

```python
sc.identify()                               # Flash the LED
sc.check_for_updates()                      # Trigger update check
sc.set_system_settings(rec_stop=45)         # Update system settings
sc.wifi_survey()                            # Scan for WiFi networks
sc.connect_wifi("MyNetwork", "password")
sc.show_vu_meter()
sc.hide_vu_meter()
```

### Managing multiple devices

```python
from sugarcube_client import SugarCubeManager

mgr = SugarCubeManager()
mgr.add("living_room", "http://10.10.0.168")
mgr.add("studio",      "http://10.10.0.169")

mgr["living_room"].pair("1111")
mgr["studio"].pair("2222")

for name, sc in mgr.items():
    status = sc.get_audio_status()
    print(name, status["recording_state"])
```

---

## Authentication Flow

On each invocation the script authenticates using the first method that succeeds:

1. `--cookie` flag (command line)
2. `cookie` field in `~/.sugarcube.json` for the named device
3. Auto-pair (no PIN required — works if the device allows it)
4. PIN from `--pin` flag or `pin` field in config

After a successful PIN pairing the session cookie is written back to `~/.sugarcube.json` automatically, so the PIN is only ever needed once.

---

## Tips

**One-off command without touching the config:**
```bash
python sugarcube_client.py --url http://10.10.0.168 --pin 1111 status
```

**Pipe JSON output into `jq` for inspection:**
```bash
python sugarcube_client.py status --json | jq '.recording_state'
```

**Scripting a noise learning workflow:**
```bash
# Drop the needle on a silent groove, then:
python sugarcube_client.py denoise --learn
sleep 10
python sugarcube_client.py denoise --stop-learn
python sugarcube_client.py denoise --on --level 8
```

**Check for clipping after a recording session:**
```bash
python sugarcube_client.py clipping
```

---

## Related

- `sugarcube_menubar.py` — macOS menu bar app that uses this client. See `README_menubar.md`.
