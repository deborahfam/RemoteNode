
import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


SESSION_PREFIX = "rnode"
CAPTURE_LINES = 150


@dataclass
class SessionInfo:
    name: str
    label: str
    alive: bool = True


class SessionManager:

    def __init__(self) -> None:
        self._tmux = shutil.which("tmux")
        self._snapshots: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return self._tmux is not None

    def _run(self, *args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._tmux, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    async def _arun(self, *args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(self._run, *args, timeout=timeout)

    def _session_name(self, label: str) -> str:
        return f"{SESSION_PREFIX}_{label}"

    async def _active_pane_target(self, label: str) -> str:
        name = self._session_name(label)
        result = await self._arun("list-panes", "-t", name, "-F", "#{pane_id} #{pane_active}")
        if result.returncode != 0:
            return name
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == "1":
                return parts[0]
        first = result.stdout.strip().splitlines()
        if first:
            return first[0].split()[0]
        return name





    async def create(self, label: str, command: Optional[str] = None) -> str:
        name = self._session_name(label)
        await self.kill(label)
        args = ["new-session", "-d", "-s", name, "-x", "200", "-y", "50"]
        if command:
            args.append(command)
        await self._arun(*args)
        self._snapshots[label] = ""
        return name

    async def kill(self, label: str) -> None:
        name = self._session_name(label)
        self._snapshots.pop(label, None)
        await self._arun("kill-session", "-t", name)

    async def is_alive(self, label: str) -> bool:
        name = self._session_name(label)
        result = await self._arun("has-session", "-t", name)
        return result.returncode == 0





    async def send_text(self, label: str, text: str) -> None:
        target = await self._active_pane_target(label)


        await self._arun("send-keys", "-t", target, "C-u")
        await self._arun("send-keys", "-t", target, "-l", text)


        await asyncio.sleep(0.12)
        await self._arun("send-keys", "-t", target, "-l", "\r")
        await asyncio.sleep(0.25)
        await self._arun("send-keys", "-t", target, "-l", "\r")

    async def send_keys(self, label: str, keys: str) -> None:
        target = await self._active_pane_target(label)
        await self._arun("send-keys", "-t", target, keys)

    async def capture(self, label: str, lines: int = CAPTURE_LINES) -> str:
        target = await self._active_pane_target(label)
        result = await self._arun(
            "capture-pane", "-t", target, "-p", "-S", f"-{lines}"
        )
        if result.returncode != 0:
            return f"(error: {result.stderr.strip()})"
        return result.stdout.rstrip("\n")

    async def capture_new(self, label: str, lines: int = CAPTURE_LINES) -> Optional[str]:
        full = await self.capture(label, lines)
        prev = self._snapshots.get(label, "")
        self._snapshots[label] = full

        if full == prev:
            return None

        if prev and full.startswith(prev):
            return full[len(prev):].lstrip("\n")

        if prev:
            prev_lines = prev.splitlines()
            full_lines = full.splitlines()
            overlap = 0
            for i in range(min(len(prev_lines), len(full_lines)), 0, -1):
                if prev_lines[-i:] == full_lines[:i]:
                    overlap = i
                    break
            if overlap:
                return "\n".join(full_lines[overlap:])

        return full

    async def prime_snapshot(self, label: str, lines: int = CAPTURE_LINES) -> None:
        self._snapshots[label] = await self.capture(label, lines)

    def reset_snapshot(self, label: str) -> None:
        self._snapshots.pop(label, None)





    async def list_sessions(self) -> list[SessionInfo]:
        result = await self._arun(
            "list-sessions", "-F", "#{session_name}"
        )
        if result.returncode != 0:
            return []
        sessions: list[SessionInfo] = []
        for line in result.stdout.strip().splitlines():
            name = line.strip()
            if name.startswith(SESSION_PREFIX + "_"):
                label = name[len(SESSION_PREFIX) + 1:]
                sessions.append(SessionInfo(name=name, label=label))
        return sessions
