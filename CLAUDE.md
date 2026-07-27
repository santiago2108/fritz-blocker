# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Two interfaces for blocking/unblocking devices on an AVM FritzBox router via the TR-064 API (`fritzconnection` library):

- `fritz_block.py` — desktop GUI (Tkinter)
- `fritz_blocker_webapp/app.py` — local web app (Flask), designed for Chromebook + phone access

Both share the same core logic: connect to the router, list hosts, and toggle WAN access per IP using the `X_AVM-DE_HostFilter1` TR-064 service.

## Running

**Desktop GUI:**
```bash
python fritz_block.py
python fritz_block.py --ip 192.168.178.1 --user myuser
```

**Web app:**
```bash
cd fritz_blocker_webapp
python3 app.py
# Opens http://127.0.0.1:5000 automatically
```

**Virtual environment** (if using `fritzenv/`):
```bash
source fritzenv/bin/activate
```

## Install dependencies

```bash
pip install fritzconnection          # for fritz_block.py
pip3 install flask fritzconnection --break-system-packages  # for web app
```

## Router configuration

- `ROUTER_IP` and `ROUTER_USER` are hardcoded at the top of `fritz_blocker_webapp/app.py` (currently `192.168.178.1` / `fritz5913`). Update these if the router setup changes.
- The desktop GUI prompts for both username and password at startup; the web app prompts only for the password (username is hardcoded).
- TR-064 must be enabled on the router: FritzBox UI → Home Network → Network → Network Settings → "Allow access for applications".
- Devices should have fixed IP leases for blocks to remain stable across DHCP renewals.

## Architecture

### Shared core (duplicated in both files)

`get_devices(fh)`, `is_blocked(fc, ip)`, and `set_blocked(fc, ip, block)` are implemented identically in both `fritz_block.py` and `app.py`. Any bug fix or change to blocking logic needs to be applied in both places.

### Desktop GUI (`fritz_block.py`)

`BlockerApp(tk.Tk)` builds a scrollable device list. Each refresh (`build_ui`) re-fetches all devices and their blocked status from the router — there is no local cache. The `ToolTip` class manages hover popups using a shared instance per row to avoid duplicate popups across child widgets.

### Web app (`fritz_blocker_webapp/app.py`)

Flask server with a session-based auth model. `SESSIONS` (in-memory dict) maps session IDs to live `FritzConnection`/`FritzHosts` objects. The UI is a single-page app (HTML + vanilla JS inlined in `APP_PAGE`), which calls two JSON endpoints:
- `GET /api/devices` — returns device list with blocked status
- `POST /api/block` — accepts `{ips: [...], block: bool}`

Restarting the server clears all sessions. The `.desktop` file and `start_fritz_blocker.sh` exist for launching from the ChromeOS app launcher.
