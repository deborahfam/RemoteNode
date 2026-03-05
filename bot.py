#!/usr/bin/env python3

import asyncio
import atexit
import fcntl
import html
import logging
import os
import re
import signal
import subprocess
import sys
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from session_manager import SESSION_PREFIX, SessionManager

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("RemoteNode")





BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_IDS: set[int] = set()

_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
for _id in _raw_ids.split(","):
    _id = _id.strip()
    if _id.isdigit():
        ALLOWED_IDS.add(int(_id))

if not BOT_TOKEN:
    sys.exit("TELEGRAM_BOT_TOKEN is not set. Check your .env file.")
if not ALLOWED_IDS:
    logger.warning("ALLOWED_USER_IDS is empty — nobody will be able to use the bot.")

TELEGRAM_MSG_LIMIT = 4096
STREAM_POLL_INTERVAL = 0.5
STREAM_SEND_DELAY = 5.0

STREAM_IDLE_TIMEOUT: Optional[float] = None

sessions = SessionManager()


_attached: dict[int, str] = {}


_watchers: dict[int, asyncio.Task] = {}


_active_procs: dict[int, asyncio.subprocess.Process] = {}
_active_tasks: dict[int, asyncio.Task] = {}
_instance_lock_handle = None





TELEGRAM_SAFE_LIMIT = TELEGRAM_MSG_LIMIT - 100


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in ALLOWED_IDS


def split_message(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def code_block(text: str) -> str:
    escaped = html.escape(text)
    return f"<pre>{escaped}</pre>"


def truncate_tail(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return "…(truncated)…\n" + text[-limit:]


def clean_mobile_output(text: str) -> str:
    lines = text.replace("\r", "\n").splitlines()
    cleaned: list[str] = []

    box_chars = set("╭╮╰╯│─")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue


        if all(ch in box_chars or ch.isspace() for ch in stripped):
            continue


        if "/run/media/" in stripped:
            continue
        if "no sandbox" in stripped and "context left" in stripped:
            continue
        if re.match(r"^Using \d+ .* file$", stripped):
            continue

        cleaned.append(line.rstrip())

    return "\n".join(cleaned).strip()


def extract_latest_completed_gemini_reply(text: str) -> Optional[str]:
    if not text:
        return None

    lines = text.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(✦|●)\s+", stripped):
            start_idx = i

    if start_idx == -1:
        return None

    collected: list[str] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue

        if re.match(r"^(>|❯)\s*", stripped):
            break
        if "Type your message or @path/to/file" in stripped:
            break
        if stripped in {"? for shortcuts"}:
            break
        if re.match(r"^Using \d+ .* file$", stripped):
            continue
        if "esc to cancel" in stripped:
            continue
        if stripped.startswith("─"):
            continue
        collected.append(line.rstrip())

    result = "\n".join(collected).strip()
    return result or None


def main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📋 Sessions", callback_data="sessions"),
            InlineKeyboardButton("🔌 Detach", callback_data="detach"),
        ],
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("🛑 Stop /cmd", callback_data="stop_cmd"),
        ],
        [
            InlineKeyboardButton("Ctrl+C", callback_data="key_ctrl_c"),
            InlineKeyboardButton("Ctrl+D", callback_data="key_ctrl_d"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)






def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            logger.warning("Unauthorized: %s", update.effective_user)
            return
        return await func(update, context)
    return wrapper






async def _watch_output(chat_id: int, label: str, bot) -> None:
    idle_seconds = 0.0
    baseline = clean_mobile_output(await sessions.capture(label, lines=220))
    last_sent_reply = extract_latest_completed_gemini_reply(baseline)
    pending_reply: Optional[str] = None
    pending_since: Optional[float] = None
    while True:
        await asyncio.sleep(STREAM_POLL_INTERVAL)

        if not await sessions.is_alive(label):
            await bot.send_message(
                chat_id=chat_id,
                text=f"🔴 Session <code>{html.escape(label)}</code> ended.",
                parse_mode=ParseMode.HTML,
            )
            _attached.pop(chat_id, None)
            break

        full_output = await sessions.capture(label, lines=220)
        mobile_output = clean_mobile_output(full_output)
        latest_reply = extract_latest_completed_gemini_reply(mobile_output)
        if not latest_reply:
            idle_seconds += STREAM_POLL_INTERVAL
            if STREAM_IDLE_TIMEOUT is not None and idle_seconds >= STREAM_IDLE_TIMEOUT:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💤 No output for {int(STREAM_IDLE_TIMEOUT)}s — pausing auto-stream.\n"
                        f"Use /peek {html.escape(label)} to check manually, "
                        f"or /attach {html.escape(label)} to resume."
                    ),
                    parse_mode=ParseMode.HTML,
                )
                break
            continue

        if latest_reply == last_sent_reply:
            pending_reply = None
            pending_since = None
            continue

        now = asyncio.get_running_loop().time()
        if latest_reply != pending_reply:
            pending_reply = latest_reply
            pending_since = now
            continue

        if pending_since is None or (now - pending_since) < STREAM_SEND_DELAY:
            continue

        idle_seconds = 0.0
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=code_block(truncate_tail(pending_reply)),
                parse_mode=ParseMode.HTML,
            )
            last_sent_reply = pending_reply
            pending_reply = None
            pending_since = None
        except Exception as exc:
            logger.error("Failed to send output: %s", exc)

    _watchers.pop(chat_id, None)


def _start_watcher(chat_id: int, label: str, bot) -> None:
    old = _watchers.pop(chat_id, None)
    if old and not old.done():
        old.cancel()
    _watchers[chat_id] = asyncio.create_task(_watch_output(chat_id, label, bot))


def _stop_watcher(chat_id: int) -> None:
    task = _watchers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


def _acquire_single_instance_lock() -> None:
    global _instance_lock_handle
    lock_path = "/tmp/remotenode_bot.lock"
    handle = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        sys.exit("Another RemoteNode instance is already running.")
    handle.write(str(os.getpid()))
    handle.flush()
    _instance_lock_handle = handle

    def _cleanup() -> None:
        try:
            if _instance_lock_handle:
                fcntl.flock(_instance_lock_handle.fileno(), fcntl.LOCK_UN)
                _instance_lock_handle.close()
        except Exception:
            pass

    atexit.register(_cleanup)






@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>RemoteNode</b> 🖥️➡️📱\n\n"
        "Control your PC terminal from Telegram.\n\n"
        "<b>Interactive sessions (tmux):</b>\n"
        "/open &lt;label&gt; &lt;command&gt; — Start a session (e.g. <code>/open ai claude</code>)\n"
        "/attach &lt;label&gt; — Attach: your messages become terminal input\n"
        "/detach — Stop forwarding messages\n"
        "/peek &lt;label&gt; — View current session output\n"
        "/send &lt;label&gt; &lt;text&gt; — Send text without attaching\n"
        "/key &lt;label&gt; &lt;key&gt; — Send special key (C-c, C-d, Enter…)\n"
        "/close &lt;label&gt; — Kill a session\n"
        "/sessions — List active sessions\n\n"
        "<b>Quick commands:</b>\n"
        "/cmd &lt;command&gt; — Run a one-off command (streamed)\n"
        "/stop — Abort running /cmd process\n"
        "/menu — Button panel\n\n"
        "<b>Workflow:</b> <code>/open ai claude</code> → <code>/attach ai</code> → "
        "just type your messages and the AI responses come back here."
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
    )


@authorized
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    label = _attached.get(update.effective_chat.id)
    status = f"Attached to: <code>{html.escape(label)}</code>" if label else "Not attached"
    await update.message.reply_text(
        status, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
    )






@authorized
async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not sessions.available:
        await update.message.reply_text("tmux is not installed on this system.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /open <label> [command]\nExample: /open ai claude")
        return

    label = context.args[0]
    command = " ".join(context.args[1:]) if len(context.args) > 1 else None
    name = await sessions.create(label, command)
    chat_id = update.effective_chat.id

    _attached[chat_id] = label
    _start_watcher(chat_id, label, context.bot)

    cmd_display = html.escape(command) if command else "shell"
    await update.message.reply_text(
        f"🚀 Session <code>{html.escape(label)}</code> started ({cmd_display}).\n"
        f"You are now <b>attached</b> — your text messages go straight to this terminal.\n"
        f"On your PC, view the same terminal with:\n"
        f"<code>tmux attach -t {html.escape(name)}</code>\n"
        f"Use /detach to stop, or /close {html.escape(label)} to terminate.",
        parse_mode=ParseMode.HTML,
    )


@authorized
async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /close <label>")
        return
    label = context.args[0]
    chat_id = update.effective_chat.id

    if _attached.get(chat_id) == label:
        _attached.pop(chat_id, None)
        _stop_watcher(chat_id)

    await sessions.kill(label)
    await update.message.reply_text(f"Session <code>{html.escape(label)}</code> closed.", parse_mode=ParseMode.HTML)


@authorized
async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not sessions.available:
        await update.message.reply_text("tmux is not installed.")
        return
    active = await sessions.list_sessions()
    chat_id = update.effective_chat.id
    current = _attached.get(chat_id)

    if not active:
        await update.message.reply_text("No active RemoteNode sessions.")
        return

    lines = []
    for s in active:
        marker = " ← attached" if s.label == current else ""
        lines.append(f"• <code>{html.escape(s.label)}</code>{marker}")
    await update.message.reply_text(
        "<b>Active sessions:</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )






@authorized
async def cmd_attach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /attach <label>")
        return
    label = context.args[0]
    if not await sessions.is_alive(label):
        await update.message.reply_text(f"Session '{label}' not found. Use /open to create one.")
        return

    chat_id = update.effective_chat.id
    _attached[chat_id] = label
    _start_watcher(chat_id, label, context.bot)

    await update.message.reply_text(
        f"🔗 Attached to <code>{html.escape(label)}</code>. Your text messages now go to this terminal.\n"
        f"On your PC, view the same terminal with:\n"
        f"<code>tmux attach -t {html.escape(f'{SESSION_PREFIX}_{label}')}</code>",
        parse_mode=ParseMode.HTML,
    )


@authorized
async def cmd_detach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    label = _attached.pop(chat_id, None)
    _stop_watcher(chat_id)
    if label:
        await update.message.reply_text(
            f"🔌 Detached from <code>{html.escape(label)}</code>. "
            "Text messages are no longer forwarded. The session keeps running.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("You weren't attached to any session.")


@authorized
async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /send <label> <text>")
        return
    label = context.args[0]
    text = " ".join(context.args[1:])
    if not await sessions.is_alive(label):
        await update.message.reply_text(f"Session '{label}' not found.")
        return
    await sessions.send_text(label, text)
    await update.message.reply_text(f"📨 Sent to <code>{html.escape(label)}</code>.", parse_mode=ParseMode.HTML)


@authorized
async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /key <label> <key>\nExamples: C-c, C-d, Enter, Up, Down")
        return
    label = context.args[0]
    key = " ".join(context.args[1:])
    if not await sessions.is_alive(label):
        await update.message.reply_text(f"Session '{label}' not found.")
        return
    await sessions.send_keys(label, key)
    await update.message.reply_text(f"⌨️ Sent <code>{html.escape(key)}</code> to {html.escape(label)}.", parse_mode=ParseMode.HTML)


@authorized
async def cmd_peek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /peek <label>")
        return
    label = context.args[0]
    if not await sessions.is_alive(label):
        await update.message.reply_text(f"Session '{label}' not found.")
        return
    output = clean_mobile_output(await sessions.capture(label))
    if not output.strip():
        output = "(no output yet)"
    for chunk in split_message(output):
        await update.message.reply_text(code_block(chunk), parse_mode=ParseMode.HTML)






@authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    label = _attached.get(chat_id)

    if not label:
        await update.message.reply_text(
            "No session attached. Use /open or /attach first, or /cmd for one-off commands."
        )
        return

    if not await sessions.is_alive(label):
        _attached.pop(chat_id, None)
        _stop_watcher(chat_id)
        await update.message.reply_text(
            f"Session <code>{html.escape(label)}</code> is no longer running.",
            parse_mode=ParseMode.HTML,
        )
        return

    text = update.message.text
    for attempt in range(2):
        try:
            await sessions.send_text(label, text)
            logger.info(
                "Forwarded Telegram text to session=%s chat_id=%s (len=%s)",
                label, chat_id, len(text),
            )
            break
        except Exception as exc:
            logger.warning(
                "Failed forwarding text to session=%s chat_id=%s attempt=%s: %s",
                label, chat_id, attempt + 1, exc,
            )
            if attempt == 0:
                await asyncio.sleep(0.15)
                continue
            await update.message.reply_text(
                "No pude enviar ese mensaje al terminal. Intenta de nuevo."
            )






@authorized
async def cmd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /cmd <command>")
        return

    command = " ".join(context.args)
    chat_id = update.effective_chat.id

    if chat_id in _active_tasks and not _active_tasks[chat_id].done():
        await update.message.reply_text("A /cmd process is already running. Use /stop first.")
        return

    status_msg = await update.message.reply_text(
        f"⏳ <code>{html.escape(command)}</code>",
        parse_mode=ParseMode.HTML,
    )
    task = asyncio.create_task(_stream_cmd(chat_id, command, status_msg, context))
    _active_tasks[chat_id] = task


async def _stream_cmd(chat_id, command, status_msg, context) -> None:
    buf: list[str] = []
    last_sent = ""

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=os.setsid if sys.platform != "win32" else None,
        )
        _active_procs[chat_id] = proc

        async def reader():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                buf.append(line.decode(errors="replace"))

        read_task = asyncio.create_task(reader())

        while not read_task.done():
            await asyncio.sleep(STREAM_POLL_INTERVAL)
            accumulated = "".join(buf)
            display = truncate_tail(accumulated)
            if display and display != last_sent:
                try:
                    await status_msg.edit_text(
                        f"⏳ <code>{html.escape(command)}</code>\n\n"
                        + code_block(display),
                        parse_mode=ParseMode.HTML,
                    )
                    last_sent = display
                except Exception:
                    pass

        await read_task
        await proc.wait()

    except asyncio.CancelledError:
        if chat_id in _active_procs:
            _kill_proc(_active_procs[chat_id])
        await status_msg.edit_text(
            f"🛑 Cancelled: <code>{html.escape(command)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:
        await status_msg.edit_text(
            f"❌ Error: <code>{html.escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    finally:
        _active_procs.pop(chat_id, None)
        _active_tasks.pop(chat_id, None)

    exit_code = proc.returncode
    icon = "✅" if exit_code == 0 else "⚠️"
    accumulated = "".join(buf)
    if not accumulated.strip():
        accumulated = "(no output)"

    chunks = split_message(accumulated)
    try:
        await status_msg.edit_text(
            f"{icon} exit {exit_code}: <code>{html.escape(command)}</code>\n\n"
            + code_block(chunks[0]),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    for chunk in chunks[1:]:
        await context.bot.send_message(
            chat_id=chat_id,
            text=code_block(chunk),
            parse_mode=ParseMode.HTML,
        )

    if len(accumulated) > 500:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Done: <code>{html.escape(command)}</code> → exit {exit_code}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


def _kill_proc(proc) -> None:
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, OSError):
        pass


@authorized
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    task = _active_tasks.get(chat_id)
    proc = _active_procs.get(chat_id)
    if task and not task.done():
        task.cancel()
        if proc:
            _kill_proc(proc)
        await update.message.reply_text("🛑 /cmd process stopped.")
    else:
        await update.message.reply_text("No active /cmd process.")






@authorized
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data == "sessions":
        active = await sessions.list_sessions() if sessions.available else []
        current = _attached.get(chat_id)
        if not active:
            await query.edit_message_text("No active sessions.", reply_markup=main_menu_keyboard())
        else:
            lines = []
            for s in active:
                marker = " ← attached" if s.label == current else ""
                lines.append(f"• <code>{html.escape(s.label)}</code>{marker}")
            await query.edit_message_text(
                "<b>Sessions:</b>\n" + "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )

    elif data == "detach":
        label = _attached.pop(chat_id, None)
        _stop_watcher(chat_id)
        msg = f"🔌 Detached from {label}." if label else "Not attached."
        await query.edit_message_text(msg, reply_markup=main_menu_keyboard())

    elif data == "stop_cmd":
        task = _active_tasks.get(chat_id)
        proc = _active_procs.get(chat_id)
        if task and not task.done():
            task.cancel()
            if proc:
                _kill_proc(proc)
            await query.edit_message_text("🛑 Process stopped.", reply_markup=main_menu_keyboard())
        else:
            await query.edit_message_text("No active /cmd process.", reply_markup=main_menu_keyboard())

    elif data == "status":
        await _inline_quick_cmd(query, "uptime && echo '---' && free -h 2>/dev/null || vm_stat 2>/dev/null")

    elif data == "key_ctrl_c":
        label = _attached.get(chat_id)
        if label and await sessions.is_alive(label):
            await sessions.send_keys(label, "C-c")
            await query.edit_message_text(
                f"Sent Ctrl+C to <code>{html.escape(label)}</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )
        else:
            await query.edit_message_text("No attached session.", reply_markup=main_menu_keyboard())

    elif data == "key_ctrl_d":
        label = _attached.get(chat_id)
        if label and await sessions.is_alive(label):
            await sessions.send_keys(label, "C-d")
            await query.edit_message_text(
                f"Sent Ctrl+D to <code>{html.escape(label)}</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )
        else:
            await query.edit_message_text("No attached session.", reply_markup=main_menu_keyboard())


async def _inline_quick_cmd(query, command: str) -> None:
    try:
        result = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(result.communicate(), timeout=15)
        output = stdout.decode(errors="replace").strip() or "(no output)"
    except asyncio.TimeoutError:
        output = "(timed out)"
    except Exception as exc:
        output = f"Error: {exc}"

    text = code_block(truncate_tail(output, 3800))
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())






async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Help and menu"),
        BotCommand("open", "Start a terminal session"),
        BotCommand("attach", "Attach to a session (text → terminal)"),
        BotCommand("detach", "Stop forwarding text"),
        BotCommand("peek", "View session output"),
        BotCommand("send", "Send text to a session"),
        BotCommand("key", "Send special key (C-c, C-d…)"),
        BotCommand("close", "Kill a session"),
        BotCommand("sessions", "List active sessions"),
        BotCommand("cmd", "Run a one-off command"),
        BotCommand("stop", "Abort running /cmd"),
        BotCommand("menu", "Quick-action buttons"),
    ])
    logger.info("RemoteNode online. Authorized users: %s", ALLOWED_IDS)


def main() -> None:
    _acquire_single_instance_lock()



    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("attach", cmd_attach))
    app.add_handler(CommandHandler("detach", cmd_detach))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("key", cmd_key))
    app.add_handler(CommandHandler("peek", cmd_peek))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("cmd", cmd_cmd))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Starting RemoteNode…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
