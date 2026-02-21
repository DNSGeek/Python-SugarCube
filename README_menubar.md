# SugarCube Menu Bar App

A lightweight macOS menu bar app for controlling SweetVinyl SugarCube devices.

## Files

- `sugarcube_menubar.py` — the menu bar app
- `sugarcube_client.py`  — the underlying API client (must be in the same folder)

## Requirements

```bash
pip install rumps requests
```

> **Note:** `rumps` requires Python to be running as a real macOS app process.
> Use `pythonw` rather than `python3` if the menu bar icon doesn't appear.

## Setup

Devices are read from `~/.sugarcube.json`, the same config file used by
`sugarcube_client.py`. If you haven't set one up yet:

```bash
# Add a device
python3 sugarcube_client.py config --add living_room --url http://10.10.0.168 --pin 1111

# Set it as the default
python3 sugarcube_client.py config --default living_room
```

## Running

```bash
# Standard (shows a Terminal window in the Dock)
python3 sugarcube_menubar.py

# Preferred (no Dock icon, true background app)
pythonw sugarcube_menubar.py
```

## Run at Login (launchd)

To have the app start automatically, create a launchd plist.

1. Find your Python path:
   ```bash
   which pythonw
   # e.g. /usr/local/bin/pythonw
   ```

2. Create `~/Library/LaunchAgents/com.sugarcube.menubar.plist`:
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
     "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
       <key>Label</key>
       <string>com.sugarcube.menubar</string>
       <key>ProgramArguments</key>
       <array>
           <string>/usr/local/bin/pythonw</string>
           <string>/path/to/sugarcube_menubar.py</string>
       </array>
       <key>RunAtLoad</key>
       <true/>
       <key>KeepAlive</key>
       <true/>
       <key>StandardOutPath</key>
       <string>/tmp/sugarcube_menubar.log</string>
       <key>StandardErrorPath</key>
       <string>/tmp/sugarcube_menubar.err</string>
   </dict>
   </plist>
   ```

3. Load it:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.sugarcube.menubar.plist
   ```

4. To stop it:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.sugarcube.menubar.plist
   ```

## Menu Bar Icons

| Icon | Meaning              |
|------|----------------------|
| ◉    | Connected, idle      |
| ⏺    | Recording in progress |
| ⚠    | Device unreachable   |

## Features

- **Quick status** — route, repair on/off, denoise on/off, recording state,
  sample rate and bit depth, all visible at a glance
- **Toggle Click Repair** — flips repair on or off with one click
- **Toggle Denoise** — flips noise reduction on or off with one click
- **Start / Stop Recording** — with macOS notifications on completion
- **Multiple devices** — if more than one device is in the config, a Device
  submenu lets you switch between them; the selected device gets a checkmark
- **Auto-refresh** — status polls every 10 seconds in the background;
  use *Refresh Now* to poll immediately
- **Identify Device** — flashes the selected device's LED

## Packaging as a standalone .app (optional)

If you want a double-clickable `.app` bundle with no dependency on Terminal,
use `py2app`:

```bash
pip install py2app
cat > setup.py << 'EOF'
from setuptools import setup
APP = ['sugarcube_menubar.py']
DATA_FILES = ['sugarcube_client.py']
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'LSUIElement': True,   # hides the Dock icon
        'CFBundleName': 'SugarCube',
        'CFBundleDisplayName': 'SugarCube',
    },
    'packages': ['rumps', 'requests'],
}
setup(app=APP, data_files=DATA_FILES, options={'py2app': OPTIONS}, setup_requires=['py2app'])
EOF

python3 setup.py py2app
# Output is in ./dist/SugarCube.app
```

Then drag `SugarCube.app` to `/Applications` or add it to Login Items in
System Settings → General → Login Items.
