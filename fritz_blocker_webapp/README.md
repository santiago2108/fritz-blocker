# FritzBox Device Blocker — Web App

A one-click device blocker you reach through a browser instead of a
desktop window, so it works the same way on your Chromebook and your
phone.

## Setup (once)

1. Install dependencies:
   ```
   pip3 install flask fritzconnection --break-system-packages
   ```
2. Confirm `ROUTER_IP` and `ROUTER_USER` at the top of `app.py` match
   your home FritzBox (currently set to `192.168.178.1` / `fritz5913`).

## Running it on the Chromebook

Option A — from the terminal:
```
cd fritz_blocker_webapp
python3 app.py
```
This opens `http://127.0.0.1:5000` in a browser automatically. Enter the
router's admin password (nothing else) to log in.

Option B — as a launchable app icon:
1. Copy `fritz-blocker.desktop` into `~/.local/share/applications/`
   (adjust the `Exec=` path first if this folder isn't in `~/Downloads`).
2. It should now appear in the ChromeOS app launcher like any other
   Linux app — click it to start the server and open the browser.
3. Leave the terminal/browser tab running in the background while you
   want the app available (closing it stops the server).

## Reaching it from your Pixel 10

The Flask server runs inside the Chromebook's Linux (Crostini)
container, which by default is **not** reachable from other devices on
your WiFi — Crostini sits behind its own internal network, separate
from the Chromebook's real WiFi connection (the same reason
`ip route` earlier showed a `100.115.92.x` address instead of your
router's actual address).

To expose it to your phone:

1. On the Chromebook: **Settings → Advanced → Developers → Linux
   development environment → Port forwarding** → add a rule for port
   `5000`, protocol TCP → enable it.
2. Find the Chromebook's real WiFi IP address: click the WiFi icon in
   the shelf → your network → **Details** → IP address.
3. On the Pixel 10, connect to the same WiFi network, open Chrome, and
   go to `http://<chromebook-wifi-ip>:5000`.
4. Log in with the router password.
5. Optional: Chrome menu (⋮) → **Add to Home screen** — gives it an
   app-style icon on your phone, though it's still just opening that
   page in Chrome under the hood.

This only works while the Chromebook is on and `app.py` is running —
there's no cloud component, everything happens on your local network.

## Notes

- The router password is never stored — it's only used to open a live
  connection to the FritzBox each time you log in. Restarting the
  server logs everyone out.
- Anyone on your WiFi who knows the port could reach the login page.
  The router admin password still gates actual access, but don't
  forward this port to the public internet.
