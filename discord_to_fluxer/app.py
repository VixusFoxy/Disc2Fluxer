from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from discord_to_fluxer import config, __version__
from discord_to_fluxer.discord_api import DiscordAPI
from discord_to_fluxer.fluxer_api import FluxerAPI
from discord_to_fluxer.models import GuildInfo, GuildSettings, GuildStructure
from discord_to_fluxer.syncer import diff_structures, sync, DiffResult, SyncCancelled

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# -- Help text (## = heading, >> = bold step, !! = italic note) -----------

_DISCORD_HELP = """\
## Discord Bot Setup
!! This bot is **read-only** — it only reads your server's
!! roles, channels, and settings. Nothing is modified on Discord.
---

>> Step 1 — Create a Bot
Go to **https://discord.com/developers/applications**
Click **New Application**, name it, click **Create**.
Click `Bot` in the sidebar, then **Reset Token** and copy it.
Paste it into the **Discord Bot Token** field.
!! Keep this token secret!

>> Step 2 — Invite to Your Server
Click `Installation` in the sidebar.
Under **Installation Contexts**, uncheck `User Install`
and check `Guild Install`.
Click `OAuth2` in the sidebar.
Check `bot` under **Scopes**.
Check `View Channels` under **Bot Permissions**.
Open the generated URL, pick your server, **Authorize**.
"""

_FLUXER_HELP = """\
## Fluxer Bot Setup
!! This bot **writes** to your Fluxer server — it creates
!! roles, channels, and updates settings to match Discord.
---

>> Step 1 — Create a Bot Application
Open the Fluxer client, go to `User Settings` (**Ctrl+,**).
Scroll down to `Applications` and click **Create Application**.
Name it and create it.

>> Step 2 — Get the Bot Token
Click **Regenerate Bot Token** and copy it.
!! This is the **Bot Token**, NOT the Client Secret!
Paste it into the **Fluxer Bot Token** field.

>> Step 3 — Invite to Your Server
Scroll to `OAuth2 URL Builder`.
Under **Scopes**, check `bot`.
Scroll down to **Bot Permissions**, check `Administrator`.
Copy the **Authorize URL** below and open it in your browser.
Select the community to sync **into** and click **Authorize**.
---

## Fluxer API URL
Default: `https://api.fluxer.app/v1`
For self-hosted instances, change to your own URL.

!! This tool syncs into an **existing** server —
!! it does not create new servers.
"""


_USAGE_GUIDE = """\
## Discord to Fluxer Sync Tool
Copies your Discord server's structure (roles, channels, and
settings) into a Fluxer server.
---

## Before You Start
Create a **new community** on Fluxer to sync into.
This tool copies structure into an existing server —
it does not create one for you.
---

## Quick Start

>> 1. Create bot tokens
Click the **Instructions** buttons next to each token field
for step-by-step guides on creating your Discord and Fluxer bots.

>> 2. Save & Connect
Paste both tokens, then click **Save & Connect**.
The tool will connect to both APIs and list your servers.

>> 3. Select servers
Pick the **source** (Discord) and **destination** (Fluxer)
servers from the dropdowns, then click **Load / Refresh**.

>> 4. Review & select items
The left panel shows everything on Discord. Items marked
`[=]` are already synced. Items marked `[x]` are selected
for sync. Click items to toggle selection.
Settings marked `[~]` differ and will be **auto-synced**.

>> 5. Sync
Click **SYNC** to push selected items to Fluxer.
A confirmation dialog will show exactly what will be created.
---

## What gets synced?
  **Roles** — name, color, permissions, position
  **Channels** — text, voice, categories, position, permissions
  **Settings** — notifications, verification, system channel, etc.

!! Member data, messages, and files are **not** synced.
!! Only server structure is copied.
"""


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"Disc2Fluxer v{__version__}")
        root.minsize(720, 600)

        self.cfg = config.load()

        # API clients (created on connect).
        self.discord: DiscordAPI | None = None
        self.fluxer: FluxerAPI | None = None

        # Guild lists for dropdowns.
        self.discord_guilds: list[GuildInfo] = []
        self.fluxer_guilds: list[GuildInfo] = []

        # Loaded structures.
        self.source_struct: GuildStructure | None = None
        self.dest_struct: GuildStructure | None = None

        # Spinner state for log activity lines.
        self._spinner_after_id: str | None = None
        self._spinner_idx: int = 0

        # Cancellation event for sync.
        self._cancel_event = threading.Event()

        self._apply_dark_theme()
        self._build_ui()

    # -- Theme ------------------------------------------------------------

    # Color palette.
    _BG = "#1e1e2e"
    _BG_LIGHT = "#2a2a3d"
    _BG_INPUT = "#313145"
    _FG = "#cdd6f4"
    _FG_DIM = "#a6adc8"
    _ACCENT = "#89b4fa"
    _BORDER = "#45475a"

    def _apply_dark_theme(self) -> None:
        self.root.configure(bg=self._BG)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=self._BG, foreground=self._FG,
                         bordercolor=self._BORDER, troughcolor=self._BG_LIGHT,
                         fieldbackground=self._BG_INPUT, font=("sans-serif", 10))
        style.configure("TLabel", background=self._BG, foreground=self._FG)
        style.configure("TLabelframe", background=self._BG, foreground=self._ACCENT,
                         bordercolor=self._BORDER)
        style.configure("TLabelframe.Label", background=self._BG, foreground=self._ACCENT)
        style.configure("TEntry", fieldbackground=self._BG_INPUT, foreground=self._FG,
                         insertcolor=self._FG)
        style.configure("TButton", background=self._BG_LIGHT, foreground=self._FG,
                         bordercolor=self._BORDER)
        style.map("TButton",
                   background=[("active", self._BORDER), ("disabled", self._BG)],
                   foreground=[("disabled", self._FG_DIM)])
        style.configure("Guide.TButton", background="#a6e3a1", foreground=self._BG,
                         font=("sans-serif", 11, "bold"), padding=(12, 6))
        style.map("Guide.TButton",
                   background=[("active", "#c6f0c2")])
        style.configure("TCombobox", fieldbackground=self._BG_INPUT, foreground=self._FG,
                         selectbackground=self._ACCENT, selectforeground=self._BG)
        style.map("TCombobox",
                   fieldbackground=[("readonly", self._BG_INPUT)],
                   foreground=[("readonly", self._FG)])
        style.configure("TFrame", background=self._BG)
        style.configure("TPanedwindow", background=self._BORDER)
        style.configure("TScrollbar", background=self._BG_LIGHT, troughcolor=self._BG,
                         bordercolor=self._BORDER, arrowcolor=self._FG_DIM)
        style.configure("Treeview", background=self._BG_INPUT, foreground=self._FG,
                         fieldbackground=self._BG_INPUT, bordercolor=self._BORDER,
                         font=("monospace", 10))
        style.configure("Treeview.Heading", background=self._BG_LIGHT, foreground=self._ACCENT,
                         bordercolor=self._BORDER)
        style.map("Treeview",
                   background=[("selected", self._BORDER)],
                   foreground=[("selected", self._FG)])

        # Combobox dropdown listbox styling.
        self.root.option_add("*TCombobox*Listbox.background", self._BG_INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", self._FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", self._ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", self._BG)

    def _dark_text(self, widget: tk.Text) -> None:
        """Apply dark colors to a tk.Text widget."""
        widget.configure(
            bg=self._BG_INPUT, fg=self._FG, insertbackground=self._FG,
            selectbackground=self._ACCENT, selectforeground=self._BG,
            highlightbackground=self._BORDER, highlightcolor=self._ACCENT,
            highlightthickness=1,
        )

    # -- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        # Usage guide button.
        ttk.Button(self.root, text=">>>> USAGE GUIDE READ ME FIRST!!! <<<<", style="Guide.TButton",
                   command=self._show_usage_guide).pack(fill="x", padx=8, pady=(8, 4))

        # Token frame.
        tok_frame = ttk.LabelFrame(self.root, text="Tokens", padding=8)
        tok_frame.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(tok_frame, text="Discord Bot Token:").grid(row=0, column=0, sticky="w")
        self.discord_token_var = tk.StringVar(value=self.cfg.get("discord_token", ""))
        self.discord_token_entry = ttk.Entry(tok_frame, textvariable=self.discord_token_var, show="*", width=50)
        self.discord_token_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(tok_frame, text="\U0001f441",  width=3,
                   command=lambda: self._toggle_reveal(self.discord_token_entry)).grid(row=0, column=2)
        ttk.Button(tok_frame, text="Instructions", style="Guide.TButton",
                   command=self._show_discord_help).grid(row=0, column=3)

        ttk.Label(tok_frame, text="Fluxer Bot Token:").grid(row=1, column=0, sticky="w")
        self.fluxer_token_var = tk.StringVar(value=self.cfg.get("fluxer_token", ""))
        self.fluxer_token_entry = ttk.Entry(tok_frame, textvariable=self.fluxer_token_var, show="*", width=50)
        self.fluxer_token_entry.grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(tok_frame, text="\U0001f441", width=3,
                   command=lambda: self._toggle_reveal(self.fluxer_token_entry)).grid(row=1, column=2)
        ttk.Button(tok_frame, text="Instructions", style="Guide.TButton",
                   command=self._show_fluxer_help).grid(row=1, column=3)

        ttk.Label(tok_frame, text="Fluxer URL:").grid(row=2, column=0, sticky="w")
        self.fluxer_url_var = tk.StringVar(value=self.cfg.get("fluxer_base_url", "https://api.fluxer.app/v1"))
        ttk.Entry(tok_frame, textvariable=self.fluxer_url_var, width=50).grid(row=2, column=1, sticky="ew", padx=4)

        save_btn = ttk.Button(tok_frame, text="Save & Connect", command=self._on_save_connect)
        save_btn.grid(row=0, column=4, rowspan=3, padx=(8, 0), sticky="ns")

        tok_frame.columnconfigure(1, weight=1)

        # Server selection frame.
        sel_frame = ttk.LabelFrame(self.root, text="Servers", padding=8)
        sel_frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(sel_frame, text="Source (Discord):").grid(row=0, column=0, sticky="w")
        self.discord_guild_var = tk.StringVar()
        self.discord_guild_combo = ttk.Combobox(sel_frame, textvariable=self.discord_guild_var,
                                                 state="readonly", width=40)
        self.discord_guild_combo.grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(sel_frame, text="Destination (Fluxer):").grid(row=1, column=0, sticky="w")
        self.fluxer_guild_var = tk.StringVar()
        self.fluxer_guild_combo = ttk.Combobox(sel_frame, textvariable=self.fluxer_guild_var,
                                                state="readonly", width=40)
        self.fluxer_guild_combo.grid(row=1, column=1, sticky="ew", padx=4)

        self.load_btn = ttk.Button(sel_frame, text="Load / Refresh", command=self._on_load,
                                    state="disabled")
        self.load_btn.grid(row=0, column=2, rowspan=2, padx=(8, 0), sticky="ns")

        sel_frame.columnconfigure(1, weight=1)

        # Vertical paned window: top = panels + sync, bottom = log.
        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        # -- Top pane: side-by-side panels + sync button --
        top_pane = ttk.Frame(paned)

        panels = ttk.Frame(top_pane)
        panels.pack(fill="both", expand=True)
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)

        # Source panel with checkable treeview.
        src_frame = ttk.LabelFrame(panels, text="SOURCE (Discord) — select items to sync", padding=4)
        src_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.src_tree = ttk.Treeview(src_frame, show="tree", selectmode="none", height=12)
        src_scroll = ttk.Scrollbar(src_frame, command=self.src_tree.yview)
        self.src_tree.configure(yscrollcommand=src_scroll.set)
        self.src_tree.pack(side="left", fill="both", expand=True)
        src_scroll.pack(side="right", fill="y")
        self.src_tree.bind("<Button-1>", self._on_tree_click)

        # Destination panel (read-only status).
        dst_frame = ttk.LabelFrame(panels, text="DESTINATION (Fluxer)", padding=4)
        dst_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.dst_tree = ttk.Treeview(dst_frame, show="tree", selectmode="none", height=12)
        dst_scroll = ttk.Scrollbar(dst_frame, command=self.dst_tree.yview)
        self.dst_tree.configure(yscrollcommand=dst_scroll.set)
        self.dst_tree.pack(side="left", fill="both", expand=True)
        dst_scroll.pack(side="right", fill="y")

        panels.rowconfigure(0, weight=1)

        # Select all / none buttons + sync.
        btn_bar = ttk.Frame(top_pane)
        btn_bar.pack(pady=4)
        ttk.Button(btn_bar, text="Select All", command=self._select_all).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="Select None", command=self._select_none).pack(side="left", padx=4)
        self.sync_btn = ttk.Button(btn_bar, text="SYNC", command=self._on_sync,
                                    state="disabled")
        self.sync_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btn_bar, text="STOP", command=self._on_stop,
                                    state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        # Track checkbox states: iid -> BooleanVar.
        # Items tagged "synced" are already on dest (non-togglable).
        # Items tagged "unsynced" are selectable.
        self._check_vars: dict[str, tk.BooleanVar] = {}

        paned.add(top_pane, weight=3)

        # -- Bottom pane: status + log --
        bottom = ttk.LabelFrame(paned, text="Log", padding=4)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w")

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(bottom, variable=self.progress_var,
                                             maximum=100, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(2, 4))

        self.log_text = tk.Text(bottom, height=6, state="disabled", wrap="word",
                                font=("monospace", 9))
        self._dark_text(self.log_text)
        self.log_text.tag_configure("spinner", foreground=self._ACCENT)
        self.log_text.tag_configure("done", foreground="#a6e3a1")
        log_scroll = ttk.Scrollbar(bottom, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        paned.add(bottom, weight=2)

    # -- Help popups ------------------------------------------------------

    def _show_help_window(self, title: str, body: str) -> None:
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("640x540")
        win.configure(bg=self._BG)
        win.transient(self.root)

        text = tk.Text(win, wrap="word", font=("sans-serif", 10), padx=12, pady=12,
                       spacing1=2, spacing3=2)
        self._dark_text(text)
        scroll = ttk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=scroll.set)

        text.tag_configure("heading", font=("sans-serif", 13, "bold"), spacing1=10,
                           spacing3=4, foreground=self._ACCENT)
        text.tag_configure("step", font=("sans-serif", 10, "bold"), spacing1=8,
                           spacing3=2, foreground="#f9e2af")
        text.tag_configure("note", font=("sans-serif", 9, "italic"), foreground=self._FG_DIM)
        text.tag_configure("bold", font=("sans-serif", 10, "bold"), foreground="#cdd6f4")
        text.tag_configure("code", font=("monospace", 9), foreground="#a6e3a1",
                           background="#313145")
        text.tag_configure("sep", foreground=self._BORDER, spacing1=4, spacing3=4)

        import re as _re
        def _insert_rich(line: str, base_tags: tuple = ()) -> None:
            """Insert a line with inline **bold** and `code` markup."""
            parts = _re.split(r"(\*\*.*?\*\*|`[^`]+`)", line)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    text.insert("end", part[2:-2], ("bold",) + base_tags)
                elif part.startswith("`") and part.endswith("`"):
                    text.insert("end", part[1:-1], ("code",) + base_tags)
                else:
                    text.insert("end", part, base_tags)
            text.insert("end", "\n", base_tags)

        for line in body.splitlines():
            if line.startswith("## "):
                text.insert("end", line[3:] + "\n", "heading")
            elif line == "---":
                text.insert("end", "\u2500" * 50 + "\n", "sep")
            elif line.startswith(">> "):
                _insert_rich(line[3:], ("step",))
            elif line.startswith("!! "):
                _insert_rich(line[3:], ("note",))
            else:
                _insert_rich(line)

        text.configure(state="disabled")
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))

    def _toggle_reveal(self, entry: ttk.Entry) -> None:
        if entry.cget("show") == "*":
            entry.configure(show="")
        else:
            entry.configure(show="*")

    def _show_usage_guide(self) -> None:
        self._show_help_window("Usage Guide", _USAGE_GUIDE)

    def _show_discord_help(self) -> None:
        self._show_help_window("Discord Bot Setup", _DISCORD_HELP)

    def _show_fluxer_help(self) -> None:
        self._show_help_window("Fluxer Bot Setup", _FLUXER_HELP)

    # -- Logging ----------------------------------------------------------

    _SPINNER = ("|", "/", "-", "\\")

    def _log(self, msg: str) -> None:
        """Append a message to the log panel. Thread-safe via root.after."""
        stripped = msg.lstrip()
        if stripped.startswith(("Creating", "Updating")):
            self.root.after(0, lambda m=msg: self._log_activity(m))
        else:
            def _append(m=msg):
                self.log_text.configure(state="normal")
                self.log_text.insert("end", f"> {m}\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            self.root.after(0, _append)

    def _log_activity(self, msg: str) -> None:
        """Insert a log line with a spinning indicator."""
        self._finish_spinner()
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "  ")
        self.log_text.insert("end", self._SPINNER[0], "spinner")
        self.log_text.insert("end", f" {msg.lstrip()}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._spinner_idx = 0
        self._spinner_after_id = self.root.after(80, self._animate_spinner)

    def _animate_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(self._SPINNER)
        self.log_text.configure(state="normal")
        ranges = self.log_text.tag_ranges("spinner")
        if ranges:
            self.log_text.delete(ranges[-2], ranges[-1])
            self.log_text.insert(ranges[-2], self._SPINNER[self._spinner_idx], "spinner")
        self.log_text.configure(state="disabled")
        self._spinner_after_id = self.root.after(80, self._animate_spinner)

    def _finish_spinner(self) -> None:
        """Replace the active spinner with a checkmark."""
        if self._spinner_after_id:
            self.root.after_cancel(self._spinner_after_id)
            self._spinner_after_id = None
        self.log_text.configure(state="normal")
        ranges = self.log_text.tag_ranges("spinner")
        if ranges:
            self.log_text.delete(ranges[-2], ranges[-1])
            self.log_text.insert(ranges[-2], "\u2713", "done")
        self.log_text.configure(state="disabled")

    def _on_stop(self) -> None:
        self._cancel_event.set()
        self._log("Sync cancelled by user.")
        self._set_status("Cancelling...")

    def _set_status(self, msg: str) -> None:
        self.root.after(0, lambda: self.status_var.set(msg))

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        stop_state = "normal" if busy else "disabled"
        def _update():
            self.load_btn.configure(state=state)
            self.sync_btn.configure(state=state)
            self.stop_btn.configure(state=stop_state)
        self.root.after(0, _update)

    def _set_progress(self, current: int, total: int) -> None:
        pct = (current / total * 100) if total > 0 else 0
        self.root.after(0, lambda: self.progress_var.set(pct))
        self._set_status(f"Syncing... {current}/{total}")

    # -- Token save & connect ---------------------------------------------

    def _on_save_connect(self) -> None:
        self.cfg["discord_token"] = self.discord_token_var.get().strip()
        self.cfg["fluxer_token"] = self.fluxer_token_var.get().strip()
        self.cfg["fluxer_base_url"] = self.fluxer_url_var.get().strip()
        try:
            config.save(self.cfg)
            self._log("Tokens saved.")
        except Exception as e:
            self._log(f"Failed to save config: {e}")
            return
        self._connect()

    def _connect(self) -> None:
        """Connect to both APIs and populate guild dropdowns."""
        self._set_busy(True)
        self._set_status("Connecting...")
        self.root.after(0, lambda: self.progress_var.set(0))

        def _work():
            try:
                # Discord.
                if self.discord:
                    self.discord.close()
                self.discord = DiscordAPI(self.cfg["discord_token"], log_fn=self._log)
                self.discord_guilds = self.discord.list_guilds()
                self._log(f"Connected to Discord ({len(self.discord_guilds)} servers)")

                # Fluxer.
                if self.fluxer:
                    self.fluxer.close()
                self.fluxer = FluxerAPI(self.cfg["fluxer_token"], self.cfg["fluxer_base_url"],
                                       log_fn=self._log)
                self.fluxer_guilds = self.fluxer.list_guilds()
                self._log(f"Connected to Fluxer ({len(self.fluxer_guilds)} servers)")

                def _connected():
                    self._populate_combos()
                    self.load_btn.configure(state="normal")
                    # Sync stays disabled until structures are loaded.
                    self.sync_btn.configure(state="disabled")
                self.root.after(0, _connected)
                self._set_status("Connected")
            except Exception as e:
                self._log(f"Connection error: {e}")
                self._set_status("Connection failed")

        threading.Thread(target=_work, daemon=True).start()

    def _populate_combos(self) -> None:
        names = [g.name for g in self.discord_guilds]
        self.discord_guild_combo["values"] = names
        if names:
            self.discord_guild_combo.current(0)

        names = [g.name for g in self.fluxer_guilds]
        self.fluxer_guild_combo["values"] = names
        if names:
            self.fluxer_guild_combo.current(0)

    # -- Load structure ----------------------------------------------------

    def _on_load(self) -> None:
        src_idx = self.discord_guild_combo.current()
        dst_idx = self.fluxer_guild_combo.current()
        if src_idx < 0 or dst_idx < 0:
            messagebox.showwarning("Select servers", "Select both a source and destination server first.")
            return

        src_guild = self.discord_guilds[src_idx]
        dst_guild = self.fluxer_guilds[dst_idx]

        self._set_busy(True)
        self._set_status("Loading structures...")
        self.root.after(0, lambda: self.progress_var.set(0))

        def _work():
            try:
                self.source_struct = self.discord.fetch_structure(src_guild.id)
                self._log(f"Loaded Discord: {self.source_struct.guild.name}")
                self.dest_struct = self.fluxer.fetch_structure(dst_guild.id)
                self._log(f"Loaded Fluxer: {dst_guild.name}")
                diff = diff_structures(self.source_struct, self.dest_struct)
                self.root.after(0, lambda: self._render_panels(diff))
                self._set_status("Loaded")
            except Exception as e:
                self._log(f"Load error: {e}")
                self._set_status("Load failed")
            finally:
                self._set_busy(False)

        threading.Thread(target=_work, daemon=True).start()

    # -- Render panels -----------------------------------------------------

    _NOTIF_LABELS = {0: "All Messages", 1: "Only Mentions"}
    _FILTER_LABELS = {0: "Disabled", 1: "Members w/o Roles", 2: "All Members"}
    _VERIFY_LABELS = {0: "None", 1: "Low", 2: "Medium", 3: "High", 4: "Highest"}

    def _channel_name_by_id(self, channels: list, channel_id: str | None) -> str:
        if channel_id is None:
            return "None"
        for ch in channels:
            if ch.id == channel_id:
                return f"#{ch.name}"
        return f"#{channel_id}"

    def _settings_rows(
        self, src: GuildSettings, dst: GuildSettings | None,
    ) -> list[tuple[str, str, str]]:
        """Return (label, src_display, dst_display) rows for settings comparison."""
        src_struct = self.source_struct
        dst_struct = self.dest_struct
        src_channels = src_struct.channels if src_struct else []
        dst_channels = dst_struct.channels if dst_struct else []

        src_sys = self._channel_name_by_id(src_channels, src.system_channel_id)
        dst_sys = self._channel_name_by_id(dst_channels, dst.system_channel_id) if dst else "?"
        src_afk = self._channel_name_by_id(src_channels, src.afk_channel_id)
        dst_afk = self._channel_name_by_id(dst_channels, dst.afk_channel_id) if dst else "?"

        rows = [
            ("System Channel", src_sys, dst_sys),
            ("System Channel Flags", str(src.system_channel_flags),
             str(dst.system_channel_flags) if dst else "?"),
            ("Default Notifications",
             self._NOTIF_LABELS.get(src.default_message_notifications, str(src.default_message_notifications)),
             self._NOTIF_LABELS.get(dst.default_message_notifications, str(dst.default_message_notifications)) if dst else "?"),
            ("Content Filter",
             self._FILTER_LABELS.get(src.explicit_content_filter, str(src.explicit_content_filter)),
             self._FILTER_LABELS.get(dst.explicit_content_filter, str(dst.explicit_content_filter)) if dst else "?"),
            ("Verification Level",
             self._VERIFY_LABELS.get(src.verification_level, str(src.verification_level)),
             self._VERIFY_LABELS.get(dst.verification_level, str(dst.verification_level)) if dst else "?"),
            ("AFK Channel", src_afk, dst_afk),
            ("AFK Timeout", f"{src.afk_timeout}s",
             f"{dst.afk_timeout}s" if dst else "?"),
        ]
        return rows

    def _render_panels(self, diff: DiffResult) -> None:
        from discord_to_fluxer.syncer import _channel_key, _parent_name_map

        matched_role_names = {src.name for src, _ in diff.matched_roles}
        unsynced_role_names = {r.name for r in diff.unsynced_roles}

        matched_chan_keys = set()
        if self.source_struct:
            src_parents = _parent_name_map(self.source_struct.channels)
            for src_ch, _ in diff.matched_channels:
                matched_chan_keys.add(_channel_key(src_ch, src_parents.get(src_ch.parent_id)))

        # --- Source tree (checkable) ---
        self.src_tree.delete(*self.src_tree.get_children())
        self._check_vars.clear()

        if not self.source_struct:
            return

        # Settings section.
        src_settings = self.source_struct.settings
        dst_settings = self.dest_struct.settings if self.dest_struct else None
        if src_settings:
            settings_node = self.src_tree.insert("", "end", text="Settings (auto-synced)", open=True)
            for label, src_val, dst_val in self._settings_rows(src_settings, dst_settings):
                mark = "[=]" if src_val == dst_val else "[~]"
                self.src_tree.insert(settings_node, "end",
                                     text=f"{mark} {label}: {src_val}",
                                     tags=("synced",))

        # Roles section.
        roles_node = self.src_tree.insert("", "end", text="Roles", open=True)
        for role in sorted(self.source_struct.roles, key=lambda r: -r.position):
            synced = role.name in matched_role_names
            iid = f"role:{role.id}"
            mark = "[=]" if synced else "[x]"
            self.src_tree.insert(roles_node, "end", iid=iid, text=f"{mark} {role.name}",
                                 tags=("synced",) if synced else ("unsynced",))
            if not synced:
                var = tk.BooleanVar(value=True)
                self._check_vars[iid] = var

        # Channels section.
        chans_node = self.src_tree.insert("", "end", text="Channels", open=True)
        cats = {ch.id: ch for ch in self.source_struct.channels if ch.type == 4}
        sorted_cats = sorted(cats.values(), key=lambda c: c.position)

        # Orphan channels.
        orphans = [ch for ch in self.source_struct.channels
                   if ch.type != 4 and ch.parent_id is None]
        for ch in sorted(orphans, key=lambda c: c.position):
            key = _channel_key(ch, None)
            synced = key in matched_chan_keys
            iid = f"chan:{ch.id}"
            prefix = "#" if ch.type == 0 else "\U0001f508"
            mark = "[=]" if synced else "[x]"
            self.src_tree.insert(chans_node, "end", iid=iid, text=f"{mark} {prefix} {ch.name}",
                                 tags=("synced",) if synced else ("unsynced",))
            if not synced:
                self._check_vars[iid] = tk.BooleanVar(value=True)

        # Categories + children.
        for cat in sorted_cats:
            cat_key = _channel_key(cat, None)
            cat_synced = cat_key in matched_chan_keys
            cat_iid = f"chan:{cat.id}"
            mark = "[=]" if cat_synced else "[x]"
            self.src_tree.insert(chans_node, "end", iid=cat_iid,
                                 text=f"{mark} \U0001f4c1 {cat.name}", open=True,
                                 tags=("synced",) if cat_synced else ("unsynced", "category"))
            if not cat_synced:
                self._check_vars[cat_iid] = tk.BooleanVar(value=True)

            children = [ch for ch in self.source_struct.channels
                        if ch.parent_id == cat.id and ch.type != 4]
            for ch in sorted(children, key=lambda c: c.position):
                key = _channel_key(ch, cat.name)
                synced = key in matched_chan_keys
                ch_iid = f"chan:{ch.id}"
                prefix = "#" if ch.type == 0 else "\U0001f508"
                mark = "[=]" if synced else "[x]"
                self.src_tree.insert(cat_iid, "end", iid=ch_iid, text=f"{mark} {prefix} {ch.name}",
                                     tags=("synced",) if synced else ("unsynced",))
                if not synced:
                    self._check_vars[ch_iid] = tk.BooleanVar(value=True)

        # --- Destination tree (read-only status) ---
        self.dst_tree.delete(*self.dst_tree.get_children())

        if dst_settings:
            settings_node = self.dst_tree.insert("", "end", text="Settings", open=True)
            for label, src_val, dst_val in self._settings_rows(src_settings, dst_settings):
                mark = "[=]" if src_val == dst_val else "[~]"
                self.dst_tree.insert(settings_node, "end",
                                     text=f"{mark} {label}: {dst_val}",
                                     tags=("synced",))

        roles_node = self.dst_tree.insert("", "end", text="Roles", open=True)
        for role in sorted(self.source_struct.roles, key=lambda r: -r.position):
            if role.name in matched_role_names:
                self.dst_tree.insert(roles_node, "end", text=f"[=] {role.name}")
            else:
                self.dst_tree.insert(roles_node, "end", text=f"[ ] ({role.name})")

        chans_node = self.dst_tree.insert("", "end", text="Channels", open=True)
        for ch in sorted(orphans, key=lambda c: c.position):
            key = _channel_key(ch, None)
            if key in matched_chan_keys:
                prefix = "#" if ch.type == 0 else "\U0001f508"
                self.dst_tree.insert(chans_node, "end", text=f"[=] {prefix} {ch.name}")
            else:
                prefix = "#" if ch.type == 0 else "\U0001f508"
                self.dst_tree.insert(chans_node, "end", text=f"[ ] ({prefix} {ch.name})")

        for cat in sorted_cats:
            cat_key = _channel_key(cat, None)
            if cat_key in matched_chan_keys:
                cat_node = self.dst_tree.insert(chans_node, "end",
                                                text=f"[=] \U0001f4c1 {cat.name}", open=True)
            else:
                cat_node = self.dst_tree.insert(chans_node, "end",
                                                text=f"[ ] (\U0001f4c1 {cat.name})", open=True)
            children = [ch for ch in self.source_struct.channels
                        if ch.parent_id == cat.id and ch.type != 4]
            for ch in sorted(children, key=lambda c: c.position):
                key = _channel_key(ch, cat.name)
                if key in matched_chan_keys:
                    prefix = "#" if ch.type == 0 else "\U0001f508"
                    self.dst_tree.insert(cat_node, "end", text=f"[=] {prefix} {ch.name}")
                else:
                    prefix = "#" if ch.type == 0 else "\U0001f508"
                    self.dst_tree.insert(cat_node, "end", text=f"[ ] ({prefix} {ch.name})")

    # -- Tree checkbox interaction -----------------------------------------

    def _on_tree_click(self, event) -> None:
        """Toggle checkbox on click for unsynced items."""
        iid = self.src_tree.identify_row(event.y)
        if not iid or iid not in self._check_vars:
            return
        var = self._check_vars[iid]
        var.set(not var.get())
        self._update_check_display(iid)

        # Smart propagation.
        tags = self.src_tree.item(iid, "tags")
        if "category" in tags:
            # Toggling a category toggles all its unsynced children.
            for child_iid in self.src_tree.get_children(iid):
                if child_iid in self._check_vars:
                    self._check_vars[child_iid].set(var.get())
                    self._update_check_display(child_iid)
        else:
            # Checking a child auto-checks its parent category if unchecked.
            parent_iid = self.src_tree.parent(iid)
            if var.get() and parent_iid in self._check_vars:
                if not self._check_vars[parent_iid].get():
                    self._check_vars[parent_iid].set(True)
                    self._update_check_display(parent_iid)

    def _update_check_display(self, iid: str) -> None:
        """Update the checkbox marker in the tree item text."""
        if iid not in self._check_vars:
            return
        old_text = self.src_tree.item(iid, "text")
        checked = self._check_vars[iid].get()
        new_mark = "[x]" if checked else "[ ]"
        # Strip the old marker prefix and keep the rest.
        if old_text.startswith(("[x]", "[ ]", "[=]", "[~]")):
            rest = old_text[3:]
        else:
            rest = old_text
        self.src_tree.item(iid, text=f"{new_mark}{rest}")

    def _select_all(self) -> None:
        for iid, var in self._check_vars.items():
            var.set(True)
            self._update_check_display(iid)

    def _select_none(self) -> None:
        for iid, var in self._check_vars.items():
            var.set(False)
            self._update_check_display(iid)

    def _get_selected_role_names(self) -> set[str]:
        """Get names of roles selected for sync."""
        names = set()
        if not self.source_struct:
            return names
        role_by_id = {r.id: r for r in self.source_struct.roles}
        for iid, var in self._check_vars.items():
            if iid.startswith("role:") and var.get():
                rid = iid[5:]
                if rid in role_by_id:
                    names.add(role_by_id[rid].name)
        return names

    def _get_selected_channel_ids(self) -> set[str]:
        """Get IDs of channels selected for sync.

        If a category is unchecked, all its children are excluded
        regardless of their individual check state.
        """
        # Collect unchecked categories.
        unchecked_cats = set()
        for iid, var in self._check_vars.items():
            if iid.startswith("chan:") and not var.get():
                tags = self.src_tree.item(iid, "tags")
                if "category" in tags:
                    unchecked_cats.add(iid)

        ids = set()
        for iid, var in self._check_vars.items():
            if iid.startswith("chan:") and var.get():
                # Skip children of unchecked categories.
                parent_iid = self.src_tree.parent(iid)
                if parent_iid in unchecked_cats:
                    continue
                ids.add(iid[5:])
        return ids

    # -- Sync --------------------------------------------------------------

    def _on_sync(self) -> None:
        if not self.source_struct or not self.dest_struct:
            messagebox.showwarning("Load first", "Load structures before syncing.")
            return

        src_idx = self.discord_guild_combo.current()
        dst_idx = self.fluxer_guild_combo.current()
        src_guild = self.discord_guilds[src_idx]
        dst_guild = self.fluxer_guilds[dst_idx]

        # Capture selections before entering the thread.
        sel_roles = self._get_selected_role_names()
        sel_channels = self._get_selected_channel_ids()
        has_unsynced = bool(self._check_vars)

        if has_unsynced and not sel_roles and not sel_channels:
            messagebox.showinfo("Nothing selected", "Select at least one role or channel to sync.")
            return

        # Build confirmation summary.
        summary_parts = []
        if sel_roles:
            summary_parts.append(f"Roles: {len(sel_roles)} new")
        sel_chan_count = len(sel_channels)
        if sel_chan_count:
            summary_parts.append(f"Channels: {sel_chan_count} new")
        # Check settings diff.
        if (self.source_struct.settings and self.dest_struct.settings):
            src_s = self.source_struct.settings
            dst_s = self.dest_struct.settings
            changed_fields = []
            for label, src_val, dst_val in self._settings_rows(src_s, dst_s):
                if src_val != dst_val:
                    changed_fields.append(label)
            if changed_fields:
                summary_parts.append(f"Settings (auto-synced): {', '.join(changed_fields)}")
        if not summary_parts:
            summary_parts.append("Position updates only")
        summary = "\n".join(summary_parts)
        if not messagebox.askokcancel(
            "Confirm sync",
            f"Sync to {dst_guild.name}?\n\n{summary}",
        ):
            return

        self._cancel_event.clear()
        self._set_busy(True)
        self._set_status("Syncing...")
        self.root.after(0, lambda: self.progress_var.set(0))

        def _on_progress(current: int, total: int) -> None:
            def _update():
                self._finish_spinner()
                pct = (current / total * 100) if total > 0 else 0
                self.progress_var.set(pct)
                self.status_var.set(f"Syncing... {current}/{total}")
                if self.source_struct and self.dest_struct:
                    d = diff_structures(self.source_struct, self.dest_struct)
                    self._render_panels(d)
            self.root.after(0, _update)

        def _work():
            try:
                # Always fetch fresh destination state to avoid duplicating
                # items that were created by a previous partial sync.
                self.dest_struct = self.fluxer.fetch_structure(dst_guild.id)

                diff = sync(
                    self.discord,
                    self.fluxer,
                    src_guild.id,
                    dst_guild.id,
                    source=self.source_struct,
                    dest=self.dest_struct,
                    log_fn=self._log,
                    progress_fn=_on_progress,
                    selected_role_names=sel_roles,
                    selected_channel_ids=sel_channels,
                    cancel_event=self._cancel_event,
                )
                # Final re-fetch for accurate state.
                self.source_struct = self.discord.fetch_structure(src_guild.id)
                self.dest_struct = self.fluxer.fetch_structure(dst_guild.id)
                updated_diff = diff_structures(self.source_struct, self.dest_struct)
                def _complete():
                    self._finish_spinner()
                    self._render_panels(updated_diff)
                    self.progress_var.set(100)
                self.root.after(0, _complete)
                self._set_status("Sync complete")
            except SyncCancelled:
                self.root.after(0, self._finish_spinner)
                self._set_status("Sync cancelled")
            except Exception as e:
                self.root.after(0, self._finish_spinner)
                self._log(f"Sync error: {e}")
                self._set_status("Sync failed")
            finally:
                self._set_busy(False)

        threading.Thread(target=_work, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
