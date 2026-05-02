#!/usr/bin/env python3
"""
Gooby RCON - Minecraft Server Console GUI
A tkinter-based RCON client mimicking the Minecraft server jar GUI.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import socket
import struct
import threading
import logging
import json
import os
import sys
import time
import queue
from datetime import datetime
from pathlib import Path

LOG_FILE = "gooby_rcon.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
log = logging.getLogger("gooby_rcon")


# RCON Protocol

class RCONError(Exception):
    pass

class RCONAuthError(RCONError):
    pass

class RCONConnectionError(RCONError):
    pass


class RCONClient:
    """
    Implements the Source RCON protocol used by Minecraft servers.
    https://wiki.vg/RCON
    """

    PACKET_AUTH        = 3
    PACKET_AUTH_RESP   = 2
    PACKET_COMMAND     = 2
    PACKET_RESPONSE    = 0
    AUTH_FAIL_ID       = -1

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.host     = host
        self.port     = port
        self.password = password
        self.timeout  = timeout
        self._sock    = None
        self._lock    = threading.Lock()
        self._req_id  = 1

    # ── connection ──────────────────────────────────────────────────────────

    def connect(self):
        log.info(f"Connecting to {self.host}:{self.port}")
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
            self._sock.settimeout(self.timeout)
        except OSError as exc:
            log.error(f"Connection failed: {exc}")
            raise RCONConnectionError(f"Cannot connect to {self.host}:{self.port} — {exc}") from exc

        # authenticate
        resp_id, resp_type, _ = self._send(self.PACKET_AUTH, self.password)
        if resp_id == self.AUTH_FAIL_ID or resp_type == self.AUTH_FAIL_ID:
            log.error("RCON authentication failed — wrong password?")
            self.close()
            raise RCONAuthError("Authentication failed. Check your RCON password.")
        log.info("RCON authenticated successfully.")

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None
        log.info("RCON connection closed.")

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # send / receive

    def command(self, cmd: str) -> str:
        """Send a command and return the server's response string."""
        if not self.connected:
            raise RCONConnectionError("Not connected.")
        with self._lock:
            _, _, payload = self._send(self.PACKET_COMMAND, cmd)
            return payload

    def _next_id(self) -> int:
        self._req_id = (self._req_id % 0x7FFFFFFF) + 1
        return self._req_id

    def _send(self, pkt_type: int, payload: str):
        req_id  = self._next_id()
        encoded = payload.encode("utf-8")
        # packet = length(4) + id(4) + type(4) + payload + null + null
        data = struct.pack("<ii", req_id, pkt_type) + encoded + b"\x00\x00"
        self._sock.sendall(struct.pack("<i", len(data)) + data)
        return self._recv()

    def _recv(self):
        raw_len = self._recv_exact(4)
        length  = struct.unpack("<i", raw_len)[0]
        body    = self._recv_exact(length)
        resp_id, resp_type = struct.unpack("<ii", body[:8])
        payload = body[8:-2].decode("utf-8", errors="replace")
        return resp_id, resp_type, payload

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RCONConnectionError("Connection closed by server.")
            buf += chunk
        return buf


# Settings persistence

SETTINGS_FILE = Path("gooby_rcon_settings.json")
DEFAULT_SETTINGS = {
    "host":     "127.0.0.1",
    "port":     "25575",
    "username": "herobrine",
    "password": "",
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                log.debug(f"Settings loaded from {SETTINGS_FILE}")
                return {**DEFAULT_SETTINGS, **data}
        except Exception as exc:
            log.warning(f"Could not load settings: {exc}")
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        log.debug(f"Settings saved to {SETTINGS_FILE}")
    except Exception as exc:
        log.error(f"Could not save settings: {exc}")


# GUI

MC_BG        = "#1a1a1a"       # deep dark background
MC_CONSOLE   = "#0d0d0d"       # console pane
MC_TEXT      = "#e0e0e0"       # main text
MC_GREEN     = "#55ff55"       # Minecraft lime
MC_YELLOW    = "#ffff55"       # Minecraft yellow
MC_RED       = "#ff5555"       # Minecraft red
MC_AQUA      = "#55ffff"       # Minecraft aqua
MC_GRAY      = "#aaaaaa"       # Minecraft gray
MC_GOLD      = "#ffaa00"       # status / accent
MC_PANEL     = "#252525"       # panel bg
MC_BORDER    = "#3a3a3a"       # border
MC_INPUT_BG  = "#111111"       # command input bg
FONT_CONSOLE = ("Courier New", 10)
FONT_UI      = ("Segoe UI", 10) if sys.platform == "win32" else ("DejaVu Sans", 10)
FONT_BOLD    = ("Segoe UI", 10, "bold") if sys.platform == "win32" else ("DejaVu Sans", 10, "bold")


class OptionsDialog(tk.Toplevel):
    def __init__(self, parent, settings: dict, on_save):
        super().__init__(parent)
        self.title("Options")
        self.resizable(False, False)
        self.configure(bg=MC_PANEL)
        self.grab_set()

        self._on_save = on_save
        self._vars    = {}

        fields = [
            ("Host / IP",   "host",     False),
            ("Port",        "port",     False),
            ("Username",    "username", False),
            ("Password",    "password", True),
        ]

        pad = {"padx": 12, "pady": 6}

        tk.Label(self, text="RCON Connection Settings",
                 bg=MC_PANEL, fg=MC_GOLD,
                 font=(*FONT_BOLD[:2], "bold")).grid(
            row=0, column=0, columnspan=2, pady=(14, 8), padx=12)

        for i, (label, key, secret) in enumerate(fields, start=1):
            tk.Label(self, text=label + ":", bg=MC_PANEL,
                     fg=MC_TEXT, font=FONT_UI).grid(
                row=i, column=0, sticky="e", **pad)

            var = tk.StringVar(value=settings.get(key, ""))
            self._vars[key] = var

            show = "*" if secret else ""
            entry = tk.Entry(self, textvariable=var, show=show,
                             width=28,
                             bg=MC_INPUT_BG, fg=MC_TEXT,
                             insertbackground=MC_GREEN,
                             relief="flat",
                             highlightthickness=1,
                             highlightcolor=MC_GREEN,
                             highlightbackground=MC_BORDER,
                             font=FONT_CONSOLE)
            entry.grid(row=i, column=1, sticky="w", **pad)

        # buttons
        btn_frame = tk.Frame(self, bg=MC_PANEL)
        btn_frame.grid(row=len(fields)+1, column=0, columnspan=2, pady=(4, 14))

        tk.Button(btn_frame, text="Save", command=self._save,
                  bg=MC_GREEN, fg="#000000", activebackground="#33cc33",
                  font=FONT_BOLD, relief="flat", padx=18, pady=4,
                  cursor="hand2").pack(side="left", padx=8)

        tk.Button(btn_frame, text="Cancel", command=self.destroy,
                  bg=MC_BORDER, fg=MC_TEXT, activebackground="#555",
                  font=FONT_UI, relief="flat", padx=18, pady=4,
                  cursor="hand2").pack(side="left", padx=8)

        self._center(parent)

    def _save(self):
        data = {k: v.get().strip() for k, v in self._vars.items()}
        if not data["host"]:
            messagebox.showerror("Validation", "Host cannot be empty.", parent=self)
            return
        try:
            port = int(data["port"])
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror("Validation", "Port must be 1–65535.", parent=self)
            return
        self._on_save(data)
        self.destroy()

    def _center(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")


class GoobyRCON(tk.Tk):
    """Main application window."""

    POLL_INTERVAL_MS = 100      # UI queue drain interval
    RECONNECT_DELAY  = 5        # seconds before auto-reconnect attempt

    def __init__(self):
        super().__init__()
        self.title("Gooby RCON — Minecraft Server Console")
        self.geometry("900x600")
        self.minsize(700, 450)
        self.configure(bg=MC_BG)
        self._protocol_on_delete()

        self._settings  = load_settings()
        self._rcon      = None
        self._connected = False
        self._ui_queue  = queue.Queue()   # thread → UI messages
        self._history   = []              # command history
        self._hist_idx  = -1

        self._build_menu()
        self._build_ui()
        self._schedule_queue_drain()

        self._log_to_console(
            "Gooby RCON started. Go to Options → Options to configure your server.\n",
            color=MC_GRAY
        )
        log.info("Application started.")

    # menu

    def _build_menu(self):
        menubar = tk.Menu(self, bg=MC_PANEL, fg=MC_TEXT,
                          activebackground=MC_GREEN, activeforeground="#000")

        options_menu = tk.Menu(menubar, tearoff=0,
                               bg=MC_PANEL, fg=MC_TEXT,
                               activebackground=MC_GREEN, activeforeground="#000")
        options_menu.add_command(label="Options…",      command=self._open_options)
        options_menu.add_separator()
        options_menu.add_command(label="Clear Console", command=self._clear_console)
        options_menu.add_separator()
        options_menu.add_command(label="Exit",          command=self._on_exit)

        menubar.add_cascade(label="Options", menu=options_menu)
        self.config(menu=menubar)

    # ── UI layout

    def _build_ui(self):
        # ── top status bar ──
        status_frame = tk.Frame(self, bg=MC_PANEL, height=32)
        status_frame.pack(fill="x", side="top")
        status_frame.pack_propagate(False)

        tk.Label(status_frame, text="⛏  Gooby RCON",
                 bg=MC_PANEL, fg=MC_GOLD,
                 font=(*FONT_UI[:1], 11, "bold")).pack(side="left", padx=12)

        self._status_label = tk.Label(
            status_frame, text="● Disconnected",
            bg=MC_PANEL, fg=MC_RED, font=FONT_UI
        )
        self._status_label.pack(side="left", padx=8)

        self._host_label = tk.Label(
            status_frame, text="",
            bg=MC_PANEL, fg=MC_GRAY, font=FONT_UI
        )
        self._host_label.pack(side="left", padx=4)

        # connect/disconnect button
        self._conn_btn = tk.Button(
            status_frame, text="Connect",
            command=self._toggle_connection,
            bg=MC_GREEN, fg="#000000",
            activebackground="#33cc33",
            font=FONT_BOLD, relief="flat",
            padx=14, pady=2, cursor="hand2"
        )
        self._conn_btn.pack(side="right", padx=12, pady=4)

        # ── console output ──
        console_frame = tk.Frame(self, bg=MC_CONSOLE)
        console_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._console = scrolledtext.ScrolledText(
            console_frame,
            bg=MC_CONSOLE, fg=MC_TEXT,
            font=FONT_CONSOLE,
            relief="flat",
            state="disabled",
            wrap="word",
            cursor="arrow",
            selectbackground="#2a4a2a",
            insertbackground=MC_GREEN,
            borderwidth=0,
            padx=8, pady=6,
        )
        self._console.pack(fill="both", expand=True)

        # configure color tags
        for tag, color in [
            ("green",  MC_GREEN),
            ("yellow", MC_YELLOW),
            ("red",    MC_RED),
            ("aqua",   MC_AQUA),
            ("gray",   MC_GRAY),
            ("gold",   MC_GOLD),
            ("white",  MC_TEXT),
        ]:
            self._console.tag_config(tag, foreground=color)

        # ── bottom input row ──
        input_frame = tk.Frame(self, bg=MC_BG, pady=6)
        input_frame.pack(fill="x", side="bottom")

        tk.Label(input_frame, text=">", bg=MC_BG, fg=MC_GREEN,
                 font=(*FONT_CONSOLE[:1], 13, "bold")).pack(side="left", padx=(10, 4))

        self._cmd_var = tk.StringVar()
        self._cmd_entry = tk.Entry(
            input_frame,
            textvariable=self._cmd_var,
            bg=MC_INPUT_BG, fg=MC_GREEN,
            insertbackground=MC_GREEN,
            relief="flat",
            font=FONT_CONSOLE,
            highlightthickness=1,
            highlightcolor=MC_GREEN,
            highlightbackground=MC_BORDER,
            disabledbackground=MC_INPUT_BG,
            disabledforeground=MC_GRAY,
        )
        self._cmd_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self._cmd_entry.bind("<Return>",   self._on_send)
        self._cmd_entry.bind("<Up>",       self._history_up)
        self._cmd_entry.bind("<Down>",     self._history_down)
        self._cmd_entry.bind("<Tab>",      lambda e: "break")  # prevent focus jump

        send_btn = tk.Button(
            input_frame, text="Send",
            command=self._on_send,
            bg=MC_GREEN, fg="#000000",
            activebackground="#33cc33",
            font=FONT_BOLD, relief="flat",
            padx=16, pady=4, cursor="hand2"
        )
        send_btn.pack(side="right", padx=(0, 10))

        self._cmd_entry.focus_set()

    # ── options dialog

    def _open_options(self):
        def on_save(new_settings):
            self._settings = new_settings
            save_settings(new_settings)
            self._log_to_console("Settings saved.\n", color=MC_GOLD)

        OptionsDialog(self, self._settings, on_save)

    # ── connection management

    def _toggle_connection(self):
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        host = self._settings.get("host", "").strip()
        port_str = self._settings.get("port", "25575").strip()
        password = self._settings.get("password", "").strip()

        if not host:
            messagebox.showerror("Config Error", "No host configured.\nGo to Options → Options.")
            return
        try:
            port = int(port_str)
        except ValueError:
            messagebox.showerror("Config Error", f"Invalid port: {port_str!r}")
            return

        self._log_to_console(f"Connecting to {host}:{port}…\n", color=MC_YELLOW)
        self._conn_btn.config(state="disabled", text="Connecting…")
        self._cmd_entry.config(state="disabled")

        def _worker():
            try:
                client = RCONClient(host, port, password)
                client.connect()
                self._ui_queue.put(("connected", client))
            except (RCONAuthError, RCONConnectionError, RCONError) as exc:
                self._ui_queue.put(("error", str(exc)))
            except Exception as exc:
                log.exception("Unexpected error during connect")
                self._ui_queue.put(("error", f"Unexpected error: {exc}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _disconnect(self):
        if self._rcon:
            try:
                self._rcon.close()
            except Exception:
                pass
            self._rcon = None
        self._connected = False
        self._update_status(False)
        self._log_to_console("Disconnected.\n", color=MC_YELLOW)

    def _on_connected(self, client: RCONClient):
        self._rcon      = client
        self._connected = True
        self._update_status(True)
        host = self._settings["host"]
        port = self._settings["port"]
        self._host_label.config(text=f"{host}:{port}")
        self._log_to_console(
            f"Connected to {host}:{port}\n", color=MC_GREEN
        )
        self._cmd_entry.config(state="normal")
        self._cmd_entry.focus_set()

    def _update_status(self, connected: bool):
        if connected:
            self._status_label.config(text="● Connected",    fg=MC_GREEN)
            self._conn_btn.config(state="normal", text="Disconnect",
                                  bg=MC_RED, fg=MC_TEXT,
                                  activebackground="#cc3333")
        else:
            self._status_label.config(text="● Disconnected", fg=MC_RED)
            self._conn_btn.config(state="normal", text="Connect",
                                  bg=MC_GREEN, fg="#000000",
                                  activebackground="#33cc33")
            self._host_label.config(text="")
            self._cmd_entry.config(state="disabled")

    # ── command sending 

    def _on_send(self, event=None):
        if not self._connected:
            return "break"
        cmd = self._cmd_var.get().strip()
        if not cmd:
            return "break"

        # echo to console
        self._log_to_console(f"> {cmd}\n", color=MC_AQUA)

        # history
        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)
        self._hist_idx = -1
        self._cmd_var.set("")

        def _worker(command):
            try:
                response = self._rcon.command(command)
                self._ui_queue.put(("response", response or "(no response)"))
            except RCONConnectionError as exc:
                log.error(f"Connection lost while sending command: {exc}")
                self._ui_queue.put(("disconnected", str(exc)))
            except Exception as exc:
                log.exception("Error sending command")
                self._ui_queue.put(("error", str(exc)))

        threading.Thread(target=_worker, args=(cmd,), daemon=True).start()
        return "break"

    # ── command history

    def _history_up(self, event):
        if not self._history:
            return "break"
        if self._hist_idx == -1:
            self._hist_idx = len(self._history) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        self._cmd_var.set(self._history[self._hist_idx])
        self._cmd_entry.icursor("end")
        return "break"

    def _history_down(self, event):
        if self._hist_idx == -1:
            return "break"
        self._hist_idx += 1
        if self._hist_idx >= len(self._history):
            self._hist_idx = -1
            self._cmd_var.set("")
        else:
            self._cmd_var.set(self._history[self._hist_idx])
        self._cmd_entry.icursor("end")
        return "break"

    # ── console output ────────────────────────────────────────────────────────

    def _log_to_console(self, text: str, color: str = MC_TEXT):
        """Thread-safe: always call from UI thread only, or push to queue."""
        timestamp = datetime.now().strftime("[%H:%M:%S] ")
        tag_map = {
            MC_GREEN:  "green",
            MC_YELLOW: "yellow",
            MC_RED:    "red",
            MC_AQUA:   "aqua",
            MC_GRAY:   "gray",
            MC_GOLD:   "gold",
            MC_TEXT:   "white",
        }
        tag = tag_map.get(color, "white")

        self._console.config(state="normal")
        self._console.insert("end", timestamp, "gray")
        self._console.insert("end", text, tag)
        self._console.config(state="disabled")
        self._console.see("end")

    def _clear_console(self):
        self._console.config(state="normal")
        self._console.delete("1.0", "end")
        self._console.config(state="disabled")
        self._log_to_console("Console cleared.\n", color=MC_GRAY)

    # ── UI queue drain ────────────────────────────────────────────────────────

    def _schedule_queue_drain(self):
        self._drain_queue()
        self.after(self.POLL_INTERVAL_MS, self._schedule_queue_drain)

    def _drain_queue(self):
        try:
            while True:
                msg_type, payload = self._ui_queue.get_nowait()
                self._handle_message(msg_type, payload)
        except queue.Empty:
            pass

    def _handle_message(self, msg_type: str, payload):
        if msg_type == "connected":
            self._on_connected(payload)

        elif msg_type == "response":
            self._log_to_console(payload + "\n", color=MC_TEXT)

        elif msg_type == "error":
            self._log_to_console(f"ERROR: {payload}\n", color=MC_RED)
            log.error(f"UI error message: {payload}")
            # reset connection state on errors that imply disconnection
            if self._rcon and not self._rcon.connected:
                self._disconnect()
            else:
                # non-fatal: re-enable UI
                self._conn_btn.config(state="normal")
                if self._connected:
                    self._cmd_entry.config(state="normal")

        elif msg_type == "disconnected":
            self._log_to_console(f"Lost connection: {payload}\n", color=MC_RED)
            log.warning(f"Disconnected: {payload}")
            self._disconnect()

    # ── cleanup ───────────────────────────────────────────────────────────────

    def _protocol_on_delete(self):
        self.protocol("WM_DELETE_WINDOW", self._on_exit)

    def _on_exit(self):
        log.info("Exiting application.")
        self._disconnect()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = GoobyRCON()
    app.mainloop()
