#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import socket
import struct
import threading
import logging
import json
import sys
import queue
import webbrowser
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

BUILDID = str("0.9.4")
WIKI_LINK = str("https://github.com/GoobyFRS/Gooby_RCON/wiki")
ISSUES_LINK = str("https://github.com/GoobyFRS/Gooby_RCON/issues")
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOG_FILE = APP_ROOT / "gooby_rcon.log"
SETTINGS_FILE = APP_ROOT / "gooby_rcon_settings.json"
ICON_FILE = APP_ROOT / "icon.ico"


def setup_logging() -> logging.Logger:
    """Create a rotating logger for the app in a stable directory."""
    logger = logging.getLogger("gooby_rcon")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

log = setup_logging()

# RCON Protocol
class RCONError(Exception):
    pass

class RCONAuthError(RCONError):
    pass

class RCONConnectionError(RCONError):
    pass

class RCONClient:
    """Implement the Source-style RCON protocol used by Minecraft servers."""
    PACKET_AUTH = 3
    PACKET_AUTH_RESP = 2
    PACKET_COMMAND = 2
    PACKET_RESPONSE = 0
    AUTH_FAIL_ID = -1

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock = None
        self._lock = threading.Lock()
        self._req_id = 1
        self._recv_buffer = bytearray()

    def connect(self) -> None:
        """Open the socket and authenticate with the server."""
        log.info(f"Connecting to {self.host}:{self.port}")
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
        except OSError as exc:
            log.error(f"Connection failed: {exc}")
            raise RCONConnectionError(f"Cannot connect to {self.host}:{self.port} — {exc}") from exc

        resp_id, resp_type, _ = self._send(self.PACKET_AUTH, self.password)
        if resp_id == self.AUTH_FAIL_ID or resp_type == self.AUTH_FAIL_ID:
            log.error("RCON authentication failed — wrong password?")
            self.close()
            raise RCONAuthError("Authentication failed. Check your RCON password.")
        log.info("RCON authenticated successfully.")

    def close(self) -> None:
        """Close the active socket if it exists."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None
        self._recv_buffer.clear()
        log.info("RCON connection closed.")

    @property
    def connected(self) -> bool:
        """Return whether the socket is currently open."""
        return self._sock is not None

    def command(self, cmd: str) -> str:
        """Send a command and return the server response string."""
        if not self.connected:
            raise RCONConnectionError("Not connected.")
        with self._lock:
            _, _, payload = self._send(self.PACKET_COMMAND, cmd)
            return payload

    def _next_id(self) -> int:
        """Return the next request id in a monotonically increasing cycle."""
        self._req_id = (self._req_id % 0x7FFFFFFF) + 1
        return self._req_id

    def _send(self, pkt_type: int, payload: str):
        """Encode a packet, send it, and return the server response."""
        req_id = self._next_id()
        encoded = payload.encode("utf-8")
        data = struct.pack("<ii", req_id, pkt_type) + encoded + b"\x00\x00"
        self._sock.sendall(struct.pack("<i", len(data)) + data)
        return self._recv()

    def _recv(self):
        """Read one Source RCON packet or a fragmented multi-packet response."""
        packets: list[tuple[int, int, str]] = []

        while True:
            raw_len = self._recv_exact(4)
            length = struct.unpack("<i", raw_len)[0]
            if length < 10:
                raise RCONConnectionError(f"Invalid RCON packet length: {length}")

            body = self._recv_exact(length)
            resp_id, resp_type = struct.unpack("<ii", body[:8])
            payload = body[8:-2].decode("utf-8", errors="replace")
            packets.append((resp_id, resp_type, payload))

            if length < 4096:
                break

        if len(packets) == 1:
            return packets[0]

        resp_id, resp_type, _ = packets[0]
        combined_payload = "".join(packet_payload for _, _, packet_payload in packets)
        return resp_id, resp_type, combined_payload

    def _recv_exact(self, byte_count: int) -> bytes:
        """Read exactly the requested number of bytes, preserving buffered data."""
        while len(self._recv_buffer) < byte_count:
            chunk = self._sock.recv(byte_count - len(self._recv_buffer))
            if not chunk:
                raise RCONConnectionError("Connection closed by server.")
            self._recv_buffer.extend(chunk)

        data = bytes(self._recv_buffer[:byte_count])
        del self._recv_buffer[:byte_count]
        return data

# Persistent Settings
DEFAULT_SETTINGS = {
    "host":         "127.0.0.1",
    "port":         "25575",
    "session_name": "My Session",
    "password":     "",
}

def load_settings() -> dict:
    """Load app settings from the stable config file path."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
                if not isinstance(data, dict):
                    return dict(DEFAULT_SETTINGS)

                normalized = dict(DEFAULT_SETTINGS)
                for key, value in data.items():
                    if key == "username" and "session_name" not in data:
                        normalized["session_name"] = value
                    else:
                        normalized[key] = value

                if not normalized.get("session_name"):
                    normalized["session_name"] = DEFAULT_SETTINGS["session_name"]

                log.debug(f"Settings loaded from {SETTINGS_FILE}")
                return normalized
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            log.warning(f"Could not load settings: {exc}")
    return dict(DEFAULT_SETTINGS)

def save_settings(settings: dict) -> None:
    """Persist settings to disk in a stable location."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as file_handle:
            json.dump(settings, file_handle, indent=2)
        log.debug(f"Settings saved to {SETTINGS_FILE}")
    except OSError as exc:
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
    """Modal dialog for configuring a connection profile."""
    def __init__(self, parent: tk.Misc, settings: dict, on_save):
        """Create the settings dialog and build its fields."""
        super().__init__(parent)
        self.title("Options")
        self.resizable(False, False)
        self.configure(bg=MC_PANEL)
        self.grab_set()

        self._on_save = on_save
        self._vars: dict[str, tk.StringVar] = {}

        fields = [
            ("Session Name", "session_name", False),
            ("Host / IP", "host", False),
            ("Port", "port", False),
            ("Password", "password", True),
        ]

        pad = {"padx": 12, "pady": 6}

        tk.Label(
            self,
            text="RCON Connection Settings",
            bg=MC_PANEL,
            fg=MC_GOLD,
            font=(*FONT_BOLD[:2], "bold"),
        ).grid(row=0, column=0, columnspan=2, pady=(14, 8), padx=12)

        for row_index, (label, key, secret) in enumerate(fields, start=1):
            tk.Label(self, text=f"{label}:", bg=MC_PANEL, fg=MC_TEXT, font=FONT_UI).grid(
                row=row_index,
                column=0,
                sticky="e",
                **pad,
            )

            var = tk.StringVar(value=settings.get(key, ""))
            self._vars[key] = var

            show = "*" if secret else ""
            entry = tk.Entry(
                self,
                textvariable=var,
                show=show,
                width=28,
                bg=MC_INPUT_BG,
                fg=MC_TEXT,
                insertbackground=MC_GREEN,
                relief="flat",
                highlightthickness=1,
                highlightcolor=MC_GREEN,
                highlightbackground=MC_BORDER,
                font=FONT_CONSOLE,)
            entry.grid(row=row_index, column=1, sticky="w", **pad)

        btn_frame = tk.Frame(self, bg=MC_PANEL)
        btn_frame.grid(row=len(fields) + 1, column=0, columnspan=2, pady=(4, 14))

        tk.Button(
            btn_frame,
            text="Save",
            command=self._save,
            bg=MC_GREEN,
            fg="#000000",
            activebackground="#33cc33",
            font=FONT_BOLD,
            relief="flat",
            padx=18,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=8)

        tk.Button(
            btn_frame,
            text="Cancel",
            command=self.destroy,
            bg=MC_BORDER,
            fg=MC_TEXT,
            activebackground="#555",
            font=FONT_UI,
            relief="flat",
            padx=18,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=8)

        self._center(parent)

    def _save(self) -> None:
        """Validate the edited values and persist them via the callback."""
        data = {key: value.get().strip() for key, value in self._vars.items()}
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

    def _center(self, parent: tk.Misc) -> None:
        """Center the dialog over its parent window."""
        self.update_idletasks()
        center_x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        center_y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{center_x}+{center_y}")

class GoobyRCON(tk.Tk):
    """Main application window."""
    POLL_INTERVAL_MS = 100

    def __init__(self):
        """Initialize the main UI, state, and background queues."""
        super().__init__()
        self.title(f"Gooby_RCON — v{BUILDID}")
        self.geometry("900x600")
        self.minsize(700, 450)
        self.configure(bg=MC_BG)
        self._set_window_icon()
        self._protocol_on_delete()

        self._settings = load_settings()
        self._rcon: RCONClient | None = None
        self._connected = False
        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._history: list[str] = []
        self._hist_idx = -1

        self._build_menu()
        self._build_ui()
        self._schedule_queue_drain()

        self._log_to_console("Gooby RCON started. Go to Options → Options to configure your server.\n", 
                             color=MC_GRAY,)
        log.info("Application started.")

    def _set_window_icon(self) -> None:
        """Apply the custom app icon when the icon file exists."""
        if not ICON_FILE.exists():
            return

        try:
            self.iconbitmap(default=str(ICON_FILE))
        except tk.TclError:
            log.warning("Could not apply custom window icon from %s", ICON_FILE)

    def _build_menu(self) -> None:
        """Create the app menu bar."""
        menubar = tk.Menu(self, bg=MC_PANEL, fg=MC_TEXT, activebackground=MC_GREEN, activeforeground="#000")

        options_menu = tk.Menu(menubar, tearoff=0, bg=MC_PANEL, fg=MC_TEXT, activebackground=MC_GREEN, activeforeground="#000")
        options_menu.add_command(label="Options…", command=self._open_options)
        options_menu.add_separator()
        options_menu.add_command(label="Clear Console", command=self._clear_console)
        options_menu.add_separator()
        options_menu.add_command(label="Exit", command=self._on_exit)

        help_menu = tk.Menu(menubar, tearoff=0, bg=MC_PANEL, fg=MC_TEXT, activebackground=MC_GREEN, activeforeground="#000")
        help_menu.add_command(label="Report Issue", command=lambda: self._open_help_link(ISSUES_LINK))
        help_menu.add_command(label="Go To Wiki", command=lambda: self._open_help_link(WIKI_LINK))

        menubar.add_cascade(label="Options", menu=options_menu)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _open_help_link(self, url: str) -> None:
        """Open a project support or documentation URL in the default browser."""
        try:
            webbrowser.open_new(url)
        except Exception as exc:  # pragma: no cover - UI fallback
            log.exception("Failed to open help URL: %s", url)
            messagebox.showerror("Help Link", f"Could not open the link:\n{url}\n\nError: {exc}", parent=self)

    def _build_ui(self) -> None:
        """Construct the app layout."""
        self._build_status_bar()
        self._build_console()
        self._build_input_row()

    def _build_status_bar(self) -> None:
        """Create the top status strip."""
        status_frame = tk.Frame(self, bg=MC_PANEL, height=32)
        status_frame.pack(fill="x", side="top")
        status_frame.pack_propagate(False)

        tk.Label(status_frame, text="⛏  Gooby RCON", bg=MC_PANEL, fg=MC_GOLD, font=(*FONT_UI[:1], 11, "bold")).pack(side="left", padx=12)

        self._status_label = tk.Label(status_frame, text="● Disconnected", bg=MC_PANEL, fg=MC_RED, font=FONT_UI)
        self._status_label.pack(side="left", padx=8)

        self._host_label = tk.Label(status_frame, text="", bg=MC_PANEL, fg=MC_GRAY, font=FONT_UI)
        self._host_label.pack(side="left", padx=4)

        self._conn_btn = tk.Button(
            status_frame,
            text="Connect",
            command=self._toggle_connection,
            bg=MC_GREEN,
            fg="#000000",
            activebackground="#33cc33",
            font=FONT_BOLD,
            relief="flat",
            padx=14,
            pady=2,
            cursor="hand2",)
        self._conn_btn.pack(side="right", padx=12, pady=4)

    def _build_console(self) -> None:
        """Create the main console display."""
        console_frame = tk.Frame(self, bg=MC_CONSOLE)
        console_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._console = scrolledtext.ScrolledText(
            console_frame,
            bg=MC_CONSOLE,
            fg=MC_TEXT,
            font=FONT_CONSOLE,
            relief="flat",
            state="disabled",
            wrap="word",
            cursor="arrow",
            selectbackground="#2a4a2a",
            insertbackground=MC_GREEN,
            borderwidth=0,
            padx=8,
            pady=6,)
        self._console.pack(fill="both", expand=True)

        for tag, color in [
            ("green", MC_GREEN),
            ("yellow", MC_YELLOW),
            ("red", MC_RED),
            ("aqua", MC_AQUA),
            ("gray", MC_GRAY),
            ("gold", MC_GOLD),
            ("white", MC_TEXT),
        ]:
            self._console.tag_config(tag, foreground=color)

    def _build_input_row(self) -> None:
        """Create the command input row and send button."""
        input_frame = tk.Frame(self, bg=MC_BG, pady=6)
        input_frame.pack(fill="x", side="bottom")

        tk.Label(input_frame, text=">", bg=MC_BG, fg=MC_GREEN, font=(*FONT_CONSOLE[:1], 13, "bold")).pack(side="left", padx=(10, 4))

        self._cmd_var = tk.StringVar()
        self._cmd_entry = tk.Entry(
            input_frame,
            textvariable=self._cmd_var,
            bg=MC_INPUT_BG,
            fg=MC_GREEN,
            insertbackground=MC_GREEN,
            relief="flat",
            font=FONT_CONSOLE,
            highlightthickness=1,
            highlightcolor=MC_GREEN,
            highlightbackground=MC_BORDER,
            disabledbackground=MC_INPUT_BG,
            disabledforeground=MC_GRAY,)
        self._cmd_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self._cmd_entry.bind("<Return>", self._on_send)
        self._cmd_entry.bind("<Up>", self._history_up)
        self._cmd_entry.bind("<Down>", self._history_down)
        self._cmd_entry.bind("<Tab>", lambda event: "break")

        send_btn = tk.Button(
            input_frame,
            text="Send",
            command=self._on_send,
            bg=MC_GREEN,
            fg="#000000",
            activebackground="#33cc33",
            font=FONT_BOLD,
            relief="flat",
            padx=16,
            pady=4,
            cursor="hand2",)
        send_btn.pack(side="right", padx=(0, 10))

        self._cmd_entry.focus_set()

    # ── Options/Settings Dialog
    def _open_options(self):
        """Open the connection settings dialog and save changes."""
        def on_save(new_settings):
            self._settings = new_settings
            save_settings(new_settings)
            self._log_to_console("Settings saved.\n", color=MC_GOLD)

        OptionsDialog(self, self._settings, on_save)
    # ── Connection Management
    def _toggle_connection(self):
        """Connect or disconnect based on the current connection state."""
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """Attempt to establish an RCON connection using the saved settings."""
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
        """Close the active RCON session and reset the UI status."""
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
        """Update the app state after a successful RCON connection."""
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
        """Refresh the connection indicators and button state."""
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

    # ── User Command Sending
    def _on_send(self, event=None):
        """Send the current command to the active server and log the result."""
        if not self._connected:
            return "break"
        cmd = self._cmd_var.get().strip()
        if not cmd:
            return "break"

        self._log_to_console(f"> {cmd}\n", color=MC_AQUA)

        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)
        self._hist_idx = -1
        self._cmd_var.set("")

        client = self._rcon
        if client is None:
            self._ui_queue.put(("error", "Not connected."))
            return "break"

        def _worker(command: str, active_client: RCONClient) -> None:
            try:
                response = active_client.command(command)
                self._ui_queue.put(("response", response or "(no response)"))
            except RCONConnectionError as exc:
                log.error(f"Connection lost while sending command: {exc}")
                self._ui_queue.put(("disconnected", str(exc)))
            except Exception as exc:
                log.exception("Error sending command")
                self._ui_queue.put(("error", str(exc)))

        threading.Thread(target=_worker, args=(cmd, client), daemon=True).start()
        return "break"

    # ── Command History
    def _history_up(self, event):
        """Move backward through the command history for the input box."""
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
        """Move forward through the command history or clear the input."""
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

    # ── Console Output Rendering
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
        """Clear the visible console text while preserving the UI state."""
        self._console.config(state="normal")
        self._console.delete("1.0", "end")
        self._console.config(state="disabled")
        self._log_to_console("Console cleared.\n", color=MC_GRAY)

    # ── UI Queue Draining
    def _schedule_queue_drain(self):
        """Keep polling the UI work queue so background tasks can update the GUI."""
        self._drain_queue()
        self.after(self.POLL_INTERVAL_MS, self._schedule_queue_drain)

    def _drain_queue(self):
        """Process queued UI messages from background threads."""
        try:
            while True:
                msg_type, payload = self._ui_queue.get_nowait()
                self._handle_message(msg_type, payload)
        except queue.Empty:
            pass

    def _handle_message(self, msg_type: str, payload):
        """Dispatch background thread updates to the appropriate UI behavior."""
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

    # ── Cleanup and Exit Handling
    def _protocol_on_delete(self):
        """Route the window-close action through the app exit handler."""
        self.protocol("WM_DELETE_WINDOW", self._on_exit)

    def _on_exit(self):
        """Close the active connection and exit the application cleanly."""
        log.info("Exiting application.")
        self._disconnect()
        self.destroy()

# ── Application Entry Point
if __name__ == "__main__":
    app = GoobyRCON()
    app.mainloop()
