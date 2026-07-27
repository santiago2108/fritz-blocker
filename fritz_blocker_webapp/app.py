"""
FritzBox Device Blocker — local web app version.

Runs a small web server on your Chromebook. Open it in a browser (on the
Chromebook itself, or from your phone over WiFi) to log in with just the
router password and block/unblock devices with a click — no terminal
commands needed after the first setup.

INSTALL (one time):
    pip3 install flask fritzconnection --break-system-packages

RUN:
    python3 app.py

Then open http://127.0.0.1:5000 in a browser on this machine.
See README.md for how to reach it from your phone too.
"""

import secrets
import threading
import webbrowser

from flask import Flask, request, redirect, url_for, session, jsonify, render_template_string

from fritzconnection import FritzConnection
from fritzconnection.lib.fritzhosts import FritzHosts

# --- Fixed connection details (only the password is entered at login) ---
ROUTER_IP = "192.168.178.1"
ROUTER_USER = "fritz5913"
PORT = 5000

SERVICE = "X_AVM-DE_HostFilter1"

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# sid -> {"fc": FritzConnection, "fh": FritzHosts}
# In-memory only: restarting the server logs everyone out, which is fine
# for a small home tool like this.
SESSIONS = {}


def get_devices(fh):
    devices = []
    for h in fh.get_hosts_info():
        if not h.get("ip"):
            continue
        devices.append(
            {
                "name": h.get("name") or h.get("mac") or h["ip"],
                "ip": h["ip"],
                "mac": h.get("mac", ""),
                "active": bool(h.get("status")),
            }
        )
    return devices


def is_blocked(fc, ip):
    result = fc.call_action(
        SERVICE, "GetWANAccessByIP", arguments={"NewIPv4Address": ip}
    )
    return str(result.get("NewDisallow")) in ("1", "True", "true")


def set_blocked(fc, ip, block):
    fc.call_action(
        SERVICE,
        "DisallowWANAccessByIP",
        arguments={"NewIPv4Address": ip, "NewDisallow": bool(block)},
    )


def get_conn():
    sid = session.get("sid")
    if not sid or sid not in SESSIONS:
        return None
    return SESSIONS[sid]


LOGIN_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FritzBox Device Blocker — Login</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background:#f4f4f4;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
  .card { background:#fff; padding:32px; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.15); width:280px; }
  h1 { font-size:18px; margin:0 0 16px; }
  input[type=password] { width:100%; padding:10px; box-sizing:border-box; margin-bottom:12px;
                          border:1px solid #ccc; border-radius:4px; font-size:14px; }
  button { width:100%; padding:10px; background:#c0392b; color:#fff; border:none;
           border-radius:4px; font-size:14px; cursor:pointer; }
  button:hover { background:#a93226; }
  .error { color:#c0392b; font-size:13px; margin-bottom:12px; }
</style>
</head>
<body>
  <div class="card">
    <h1>FritzBox Device Blocker</h1>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post" action="/login">
      <input type="password" name="password" placeholder="Router admin password" autofocus required>
      <button type="submit">Log In</button>
    </form>
  </div>
</body>
</html>
"""

APP_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FritzBox Device Blocker</title>
<style>
  * { box-sizing:border-box; }
  body { font-family:-apple-system, "Segoe UI", Roboto, sans-serif; background:#f4f4f4; margin:0; padding:0; }
  header { padding:16px; text-align:center; font-weight:600; font-size:18px; background:#fff; border-bottom:1px solid #ddd; }
  #amazon-bar { padding:10px 16px 0; }
  #amazon-bar button { width:100%; padding:10px; border:none; border-radius:4px; color:#fff; font-size:14px; cursor:pointer; }
  #list { max-height:55vh; overflow-y:auto; background:#fff; margin:12px 16px; border:1px solid #ddd; border-radius:4px; }
  .row { display:flex; align-items:center; padding:10px 12px; border-bottom:1px solid #eee; }
  .row:last-child { border-bottom:none; }
  .row .name { flex:1; margin-left:10px; font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .row button { width:90px; padding:7px 0; border:none; border-radius:4px; color:#fff; font-size:13px; cursor:pointer; flex-shrink:0; }
  footer { display:flex; gap:8px; padding:4px 16px 8px; flex-wrap:wrap; }
  footer button { flex:1; min-width:120px; padding:10px; border:none; border-radius:4px; font-size:13px; cursor:pointer; color:#fff; background:#7f8c8d; }
  .block-sel { background:#c0392b !important; }
  .unblock-sel { background:#27ae60 !important; }
  .blocked-btn { background:#c0392b; }
  .unblocked-btn { background:#27ae60; }
  .refresh-wrap { text-align:center; padding-bottom:8px; }
  .refresh-wrap button { padding:8px 24px; border:none; border-radius:4px; background:#7f8c8d; color:#fff; cursor:pointer; }
  #logout { text-align:center; padding-bottom:20px; }
  #logout button { background:none; border:none; color:#888; text-decoration:underline; cursor:pointer; font-size:12px; padding:8px; }
</style>
</head>
<body>
  <header>Devices</header>
  <div id="amazon-bar"></div>
  <div id="list"></div>
  <footer>
    <button onclick="selectAll()">Select All</button>
    <button onclick="clearSel()">Clear Selection</button>
    <button class="unblock-sel" onclick="batchSet(false)">Unblock Selected</button>
    <button class="block-sel" onclick="batchSet(true)">Block Selected</button>
  </footer>
  <div class="refresh-wrap"><button onclick="loadDevices()">Refresh</button></div>
  <div id="logout"><form method="post" action="/logout"><button type="submit">Log out</button></form></div>

<script>
let devices = [];

async function loadDevices() {
  const res = await fetch('/api/devices');
  if (res.status === 401) { window.location = '/'; return; }
  devices = await res.json();
  render();
}

function render() {
  const list = document.getElementById('list');
  list.innerHTML = '';
  devices.forEach(d => {
    const row = document.createElement('div');
    row.className = 'row';
    row.title = 'IP: ' + d.ip + '\\nMAC: ' + (d.mac || 'unknown') + '\\nStatus: ' + (d.active ? 'online' : 'offline');

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.ip = d.ip;

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = d.name;

    const btn = document.createElement('button');
    btn.textContent = d.blocked ? 'Unblock' : 'Block';
    btn.className = d.blocked ? 'unblocked-btn' : 'blocked-btn';
    btn.onclick = () => blockIps([d.ip], !d.blocked);

    row.appendChild(cb);
    row.appendChild(name);
    row.appendChild(btn);
    list.appendChild(row);
  });

  const amazonIps = devices.filter(d => d.name.toLowerCase().includes('amazon')).map(d => d.ip);
  const bar = document.getElementById('amazon-bar');
  bar.innerHTML = '';
  if (amazonIps.length) {
    const allBlocked = amazonIps.every(ip => devices.find(d => d.ip === ip).blocked);
    const btn = document.createElement('button');
    btn.textContent = (allBlocked ? 'Unblock' : 'Block') + ' All Amazon Devices (' + amazonIps.length + ')';
    btn.style.background = allBlocked ? '#27ae60' : '#c0392b';
    btn.onclick = () => blockIps(amazonIps, !allBlocked);
    bar.appendChild(btn);
  }
}

function selectAll() {
  document.querySelectorAll('#list input[type=checkbox]').forEach(cb => cb.checked = true);
}
function clearSel() {
  document.querySelectorAll('#list input[type=checkbox]').forEach(cb => cb.checked = false);
}

async function blockIps(ips, block) {
  await fetch('/api/block', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ips: ips, block: block})
  });
  loadDevices();
}

function batchSet(block) {
  const ips = Array.from(document.querySelectorAll('#list input[type=checkbox]:checked')).map(cb => cb.dataset.ip);
  if (!ips.length) { alert('Check one or more devices first.'); return; }
  blockIps(ips, block);
}

loadDevices();
</script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    if get_conn():
        return redirect(url_for("devices_page"))
    return render_template_string(LOGIN_PAGE, error=request.args.get("error"))


@app.route("/login", methods=["POST"])
def login():
    password = request.form.get("password", "")
    try:
        fc = FritzConnection(address=ROUTER_IP, user=ROUTER_USER, password=password)
        fh = FritzHosts(fc)
        fh.get_hosts_info()  # sanity check the credentials actually work
    except Exception:
        return redirect(url_for("index", error="Login failed. Check the password and try again."))
    sid = secrets.token_hex(16)
    SESSIONS[sid] = {"fc": fc, "fh": fh}
    session["sid"] = sid
    return redirect(url_for("devices_page"))


@app.route("/logout", methods=["POST"])
def logout():
    sid = session.pop("sid", None)
    if sid:
        SESSIONS.pop(sid, None)
    return redirect(url_for("index"))


@app.route("/devices", methods=["GET"])
def devices_page():
    if not get_conn():
        return redirect(url_for("index"))
    return render_template_string(APP_PAGE)


@app.route("/api/devices", methods=["GET"])
def api_devices():
    conn = get_conn()
    if not conn:
        return jsonify({"error": "not logged in"}), 401
    devices = get_devices(conn["fh"])
    for d in devices:
        try:
            d["blocked"] = is_blocked(conn["fc"], d["ip"])
        except Exception:
            d["blocked"] = False
    return jsonify(devices)


@app.route("/api/block", methods=["POST"])
def api_block():
    conn = get_conn()
    if not conn:
        return jsonify({"error": "not logged in"}), 401
    data = request.get_json(force=True)
    ips = data.get("ips", [])
    block = bool(data.get("block", True))
    errors = []
    for ip in ips:
        try:
            set_blocked(conn["fc"], ip, block)
        except Exception as e:
            errors.append(f"{ip}: {e}")
    return jsonify({"ok": not errors, "errors": errors})


def main():
    url = f"http://127.0.0.1:{PORT}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
