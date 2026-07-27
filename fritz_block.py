"""
fritz_block.py — One-click device blocking for AVM FritzBox routers.

Uses the FritzBox's official TR-064 API (via the fritzconnection library)
to list devices on your network and toggle their internet access.

SETUP (one-time, on the router):
    1. Open the FritzBox web interface -> Home Network -> Network ->
       Network Settings.
    2. Enable "Allow access for applications" (this activates TR-064).
    3. Make sure the FritzBox has a configured user + password
       (System -> FRITZ!Box Users).
    4. For reliable blocking, give the devices you want to control a
       fixed IP lease: Home Network -> Network -> Network Settings ->
       edit the device -> "Always assign this network device the same
       IP address". Without this, DHCP can hand the device a new IP
       later and the block will silently stop applying to it.

INSTALL:
    pip install fritzconnection

RUN:
    python fritz_block.py
    python fritz_block.py --ip 192.168.178.1 --user myuser

The default router address for AVM FritzBoxes is 192.168.178.1.
M-net may have configured yours differently — check the label on the
device or the URL you use to reach the admin panel if unsure.
"""

import argparse
import getpass
import tkinter as tk
from tkinter import messagebox

from fritzconnection import FritzConnection
from fritzconnection.lib.fritzhosts import FritzHosts

SERVICE = "X_AVM-DE_HostFilter1"

ROW_BTN_WIDTH = 10       # Block/Unblock buttons per device row (fixed width)


def connect(address, user, password):
    fc = FritzConnection(address=address, user=user, password=password)
    fh = FritzHosts(fc)
    return fc, fh


def get_devices(fh):
    """Return known devices as a list of dicts with name, ip, mac, active."""
    devices = []
    for h in fh.get_hosts_info():
        if not h.get("ip"):
            continue  # skip entries with no current IP (currently offline)
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


def set_blocked(fc, ip, block: bool):
    fc.call_action(
        SERVICE,
        "DisallowWANAccessByIP",
        arguments={"NewIPv4Address": ip, "NewDisallow": bool(block)},
    )


def find_amazon_devices(devices):
    """Return IPs of every device whose name mentions Amazon (Echo/Alexa
    speakers etc. typically show up as 'Amazon', 'Amazon 1', 'Amazon 2'...)."""
    return [d["ip"] for d in devices if "amazon" in d["name"].lower()]


class ToolTip:
    """
    Single shared hover popup for an entire device row.

    Rather than trying to distinguish "left the row" from "moved onto a
    child widget inside the row" via low-level event details (Python's
    Tkinter Event object doesn't reliably expose that on all platforms —
    that was the cause of the AttributeError), this attaches the SAME
    ToolTip instance to the row and every child widget inside it (the
    checkbox, the name label, the button). Only one instance -> only one
    popup can ever exist. Leaving any one of those widgets schedules a
    short delayed hide; entering any other widget in the same group
    cancels that pending hide before it fires, so the popup stays visible
    continuously while the pointer is anywhere within the row and closes
    cleanly once it actually leaves the row altogether.
    """

    def __init__(self, text_fn):
        self.text_fn = text_fn
        self.tip_window = None
        self._hide_job = None
        self._owner = None  # widget used as the anchor/root for scheduling

    def attach(self, widget):
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        if self._owner is None:
            self._owner = widget

    def _on_enter(self, event=None):
        if self._hide_job:
            self._owner.after_cancel(self._hide_job)
            self._hide_job = None
        self._show()

    def _on_leave(self, event=None):
        self._hide_job = self._owner.after(80, self._hide)

    def _show(self):
        if self.tip_window or self._owner is None:
            return
        text = self.text_fn()
        x = self._owner.winfo_rootx() + 10
        y = self._owner.winfo_rooty() + self._owner.winfo_height() + 4
        self.tip_window = tw = tk.Toplevel(self._owner)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw,
            text=text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            padx=6,
            pady=4,
        ).pack()

    def _hide(self):
        self._hide_job = None
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class BlockerApp(tk.Tk):
    def __init__(self, fc, fh):
        super().__init__()
        self.fc = fc
        self.fh = fh
        self.title("FritzBox Device Blocker")
        self.geometry("560x680")
        # Minimum size chosen so the footer buttons never overlap or get
        # clipped — the window simply won't shrink past what the content
        # needs, everything above that resizes smoothly.
        self.minsize(520, 400)
        self.check_vars = {}  # ip -> tk.BooleanVar (checkbox state)

        # --- Fixed header ---
        tk.Label(
            self, text="Devices", font=("Segoe UI", 14, "bold")
        ).pack(side="top", pady=(8, 4))

        # --- Fixed footer ---
        # Built with grid + column weights (instead of fixed-width pack)
        # so the buttons scale together proportionally as the window is
        # resized, rather than overflowing or overlapping.
        footer = tk.Frame(self)
        footer.pack(side="bottom", fill="x")

        tk.Button(footer, text="Refresh", command=self.build_ui).pack(
            fill="x", padx=12, pady=(2, 6)
        )

        batch_bar = tk.Frame(footer)
        batch_bar.pack(fill="x", padx=12, pady=(0, 4))
        for col in range(4):
            batch_bar.columnconfigure(col, weight=1)

        tk.Button(batch_bar, text="Select All", command=self.select_all).grid(
            row=0, column=0, sticky="ew", padx=2
        )
        tk.Button(
            batch_bar, text="Clear Selection", command=self.clear_selection
        ).grid(row=0, column=1, sticky="ew", padx=2)
        tk.Button(
            batch_bar,
            text="Unblock Selected",
            bg="#27ae60",
            fg="white",
            command=lambda: self.batch_set(block=False),
        ).grid(row=0, column=2, sticky="ew", padx=2)
        tk.Button(
            batch_bar,
            text="Block Selected",
            bg="#c0392b",
            fg="white",
            command=lambda: self.batch_set(block=True),
        ).grid(row=0, column=3, sticky="ew", padx=2)

        # --- Quick block: Amazon devices only ---
        # A single dedicated button (not a generic per-prefix system) that
        # finds every device with "Amazon" in its name and blocks/unblocks
        # them all in one click.
        self.amazon_bar = tk.Frame(self)
        self.amazon_bar.pack(side="bottom", fill="x", padx=12, pady=(4, 0))
        self.amazon_button = tk.Button(
            self.amazon_bar, command=self.toggle_amazon_group
        )
        self.amazon_button.pack(fill="x")

        # --- Scrollable middle area (device list) ---
        list_container = tk.Frame(self)
        list_container.pack(side="top", fill="both", expand=True)

        self.canvas = tk.Canvas(list_container, highlightthickness=0)
        scrollbar = tk.Scrollbar(
            list_container, orient="vertical", command=self.canvas.yview
        )
        self.scroll_frame = tk.Frame(self.canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.scroll_frame, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Keep the inner frame the same width as the visible canvas so
        # rows stretch/resize smoothly instead of staying a fixed size
        # while the window changes around them.
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width),
        )

        # Mouse-wheel scrolling: Windows/Mac use <MouseWheel>, Linux (X11,
        # including this Crostini/Debian setup) uses Button-4/Button-5.
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

        self._amazon_ips = []
        self.build_ui()

    def _on_mousewheel(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def build_ui(self):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        self.check_vars = {}

        try:
            devices = get_devices(self.fh)
        except Exception as e:
            messagebox.showerror("Error fetching devices", str(e))
            devices = []

        # Fetch each device's blocked status once, reuse for both its row
        # button and the Amazon group button below (avoids duplicate calls).
        blocked_status = {}
        for dev in devices:
            try:
                blocked_status[dev["ip"]] = is_blocked(self.fc, dev["ip"])
            except Exception:
                blocked_status[dev["ip"]] = False

        # --- Amazon quick-block button ---
        self._amazon_ips = find_amazon_devices(devices)
        if self._amazon_ips:
            all_blocked = all(blocked_status.get(ip, False) for ip in self._amazon_ips)
            self.amazon_button.config(
                text=(
                    f"Unblock All Amazon Devices ({len(self._amazon_ips)})"
                    if all_blocked
                    else f"Block All Amazon Devices ({len(self._amazon_ips)})"
                ),
                bg="#27ae60" if all_blocked else "#c0392b",
                fg="white",
            )
            self.amazon_bar.pack(side="bottom", fill="x", padx=12, pady=(4, 0))
        else:
            self.amazon_bar.pack_forget()

        # --- Device rows (name only, details on hover) ---
        for dev in devices:
            row = tk.Frame(self.scroll_frame)
            row.pack(fill="x", padx=12, pady=3)

            var = tk.BooleanVar(value=False)
            self.check_vars[dev["ip"]] = var
            tk.Checkbutton(row, variable=var).pack(side="left")

            name_label = tk.Label(row, text=dev["name"], anchor="w")
            name_label.pack(side="left", fill="x", expand=True)

            def tooltip_text(d=dev):
                return (
                    f"IP: {d['ip']}\n"
                    f"MAC: {d['mac'] or 'unknown'}\n"
                    f"Status: {'online' if d['active'] else 'offline'}"
                )

            blocked = blocked_status.get(dev["ip"], False)
            btn = tk.Button(
                row,
                text="Unblock" if blocked else "Block",
                width=ROW_BTN_WIDTH,
                bg="#27ae60" if blocked else "#c0392b",
                fg="white",
                command=lambda ip=dev["ip"], cur=blocked: self.toggle(ip, cur),
            )
            btn.pack(side="right")

            # One shared tooltip for the whole row — attach to every
            # widget in it so hovering any part keeps the same popup
            # visible instead of spawning duplicates.
            tooltip = ToolTip(tooltip_text)
            for w in (row, name_label, btn):
                tooltip.attach(w)

    def select_all(self):
        for var in self.check_vars.values():
            var.set(True)

    def clear_selection(self):
        for var in self.check_vars.values():
            var.set(False)

    def toggle(self, ip, currently_blocked):
        try:
            set_blocked(self.fc, ip, not currently_blocked)
        except Exception as e:
            messagebox.showerror("Error", str(e))
        self.build_ui()

    def toggle_amazon_group(self):
        if not self._amazon_ips:
            return
        # Decide direction from current button label rather than
        # re-querying: "Block All..." -> block, "Unblock All..." -> unblock.
        block = self.amazon_button.cget("text").startswith("Block")
        self.block_ip_list(self._amazon_ips, block=block)

    def batch_set(self, block: bool):
        """Apply block/unblock to every checked device in one action."""
        selected_ips = [ip for ip, var in self.check_vars.items() if var.get()]
        if not selected_ips:
            messagebox.showinfo("No devices selected", "Check one or more devices first.")
            return
        self.block_ip_list(selected_ips, block=block)

    def block_ip_list(self, ip_list, block=True):
        """Block (or unblock) every IP in ip_list in one action."""
        errors = []
        for ip in ip_list:
            try:
                set_blocked(self.fc, ip, block)
            except Exception as e:
                errors.append(f"{ip}: {e}")

        if errors:
            messagebox.showerror("Some devices failed", "\n".join(errors))
        self.build_ui()


def main():
    parser = argparse.ArgumentParser(description="FritzBox one-click device blocker")
    parser.add_argument(
        "--ip", default="192.168.178.1", help="Router IP address"
    )
    parser.add_argument("--user", default=None, help="FritzBox login username")
    args = parser.parse_args()

    user = args.user or input("FritzBox username: ")
    password = getpass.getpass("FritzBox password: ")

    fc, fh = connect(args.ip, user, password)
    app = BlockerApp(fc, fh)
    app.mainloop()


if __name__ == "__main__":
    main()