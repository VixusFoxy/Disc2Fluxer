# Disc2Fluxer

[![Build](https://github.com/VixusFoxy/Disc2Fluxer/actions/workflows/build.yml/badge.svg)](https://github.com/VixusFoxy/Disc2Fluxer/actions/workflows/build.yml)

Copy your Discord server's structure (roles, channels, and settings) into a Fluxer community.

## Download

Grab the latest binary for your platform from the [Releases](../../releases) page:

- **Windows:** `discord-to-fluxer-windows.exe` — download and double-click
- **Linux:** `discord-to-fluxer-linux` — download, `chmod +x`, and run
- **macOS:** `discord-to-fluxer-macos` — download, `chmod +x`, and run

No Python or dependencies needed.

## What gets synced?

- **Roles** — name, color, permissions, position
- **Channels** — text, voice, categories, position, permissions
- **Settings** — notification level, verification level, system channel, AFK channel

Messages, members, and files are **not** synced — only server structure.

## Quick Start

1. Create a new community on Fluxer to sync into
2. Create bot applications on both Discord and Fluxer (click the **Instructions** buttons in the app for step-by-step guides)
3. Paste both bot tokens, click **Save & Connect**
4. Select your source (Discord) and destination (Fluxer) servers
5. Click **Load / Refresh** to see the diff
6. Select items to sync, click **SYNC**

## Running from source

Requires Python 3.10+ with tkinter.

```
pip install -r requirements.txt
python -m discord_to_fluxer
```

## Security

Bot tokens are **never saved to disk**. They only exist in memory for the duration of the session.

## Credits

- **Vixus** — project lead
- **Margo** — testing & feedback
- **Pura** — testing & feedback
