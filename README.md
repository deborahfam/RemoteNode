# RemoteNode

Control your PC terminal from Telegram with persistent `tmux` sessions.

RemoteNode links your Telegram chat to a terminal session on your machine:
messages go in, terminal output comes back.

## Spanish Guide

- Full Spanish step-by-step guide: [GUIA_ES.md](GUIA_ES.md)

## What It Does

- Starts remote sessions with `/open <label> <command>`
- Auto-attaches you to the session
- Forwards plain chat messages as keyboard input
- Streams new terminal output back to Telegram
- Keeps sessions alive even if the bot restarts

```text
Telegram (phone)            PC (tmux)
Your message  ----------->  Interactive session (claude/gemini/shell)
Output back   <-----------  Live terminal output
```

## Quick Start (5 minutes)

### 1) Requirements

- Python 3.10+
- `tmux` installed
- Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram numeric user ID (from [@userinfobot](https://t.me/userinfobot))

### 2) Install dependencies

```bash
cd /path/to/RemoteNode
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install `tmux` if needed:

```bash
# Arch
sudo pacman -S tmux

# Debian/Ubuntu
sudo apt install tmux

# macOS
brew install tmux
```

### 3) Configure `.env`

```bash
cp .env.example .env
nano .env
```

Required variables:

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Your bot token |
| `ALLOWED_USER_IDS` | Allowed Telegram user IDs (comma-separated) |

Example:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
ALLOWED_USER_IDS=123456789,987654321
```

### 4) Run the bot

```bash
source .venv/bin/activate
python bot.py
```

If startup is successful, logs will show the bot is online.

## Recommended First Workflow

1. In Telegram:
   - `/start`
   - `/open ai claude` (or `/open ai gemini`)
2. Send normal messages (no slash command) to interact.
3. Pause forwarding with `/detach`.
4. Re-attach with `/attach ai`.
5. Close when done with `/close ai`.

To view the same session on your PC:

```bash
tmux attach -t rnode_ai
```

## Main Commands

| Command | Purpose |
| --- | --- |
| `/open <label> <cmd>` | Create a persistent `tmux` session and auto-attach |
| `/attach <label>` | Attach to an existing session |
| `/detach` | Stop forwarding your chat text to terminal |
| `/peek <label>` | Show current output from a session |
| `/send <label> <text>` | Send text without attaching |
| `/key <label> <key>` | Send special keys (`C-c`, `C-d`, `Enter`, `Up`, `Down`) |
| `/sessions` | List active sessions |
| `/close <label>` | Terminate a session |
| `/cmd <command>` | Run one-off command (streamed output) |
| `/stop` | Stop active `/cmd` process |
| `/menu` | Open quick action buttons |

## Quick Panel (`/menu`)

Provides one-tap actions for:

- Active sessions list
- Quick detach
- System status (uptime/memory)
- Stop `/cmd`
- Send `Ctrl+C` / `Ctrl+D`

## Risks and Safety (Read This First)

RemoteNode is powerful and can be dangerous if misconfigured.

### What can go wrong

- **Unauthorized access**: if your Telegram account or bot token is compromised, an attacker may run commands on your PC.
- **Data loss**: destructive commands (`rm`, `dd`, bad scripts) can delete files permanently.
- **Privilege abuse**: if the bot runs as root or a privileged user, damage impact is much higher.
- **Secret leakage**: terminal output may include API keys, credentials, or private files, then get forwarded to Telegram.
- **Lateral movement**: if this host has SSH keys, cloud creds, or internal network access, compromise can spread.

### Required precautions

- Use a **dedicated low-privilege OS user** to run the bot.
- **Never run as root**.
- Keep `ALLOWED_USER_IDS` minimal and verified.
- Store `.env` securely; do not commit it.
- Rotate `TELEGRAM_BOT_TOKEN` immediately if leaked.
- Restrict host permissions (filesystem, network, service access).
- Avoid running sensitive commands from Telegram when possible.
- Monitor logs and review unusual activity.

### Recommended hardening

- Run in an isolated environment (container/VM).
- Add filesystem backups and snapshots before heavy use.
- Use host firewall rules to limit outbound/inbound exposure.
- Keep system and dependencies updated.
- Enable full-disk encryption for laptops/desktops.

## Run as a Service (Linux/systemd)

```bash
sudo cp remotenode.service /etc/systemd/system/
sudo nano /etc/systemd/system/remotenode.service
sudo systemctl daemon-reload
sudo systemctl enable --now remotenode
sudo journalctl -u remotenode -f
```

## Troubleshooting

### `409 Conflict`

Another bot instance is already running.

```bash
pkill -f "^python bot.py$"
source .venv/bin/activate
python bot.py
```

### Session does not respond

- Ensure you are attached: `/attach <label>`
- Check if it is alive: `/sessions`
- Verify on PC: `tmux attach -t rnode_<label>`

### No Telegram output

- Recheck token and `ALLOWED_USER_IDS` in `.env`
- Confirm process is running
- Check systemd logs if running as a service

## Project Structure

```text
RemoteNode/
├── bot.py
├── session_manager.py
├── remotenode.service
├── requirements.txt
├── GUIA_ES.md
└── README.md
```

## License

See `LICENSE`.
