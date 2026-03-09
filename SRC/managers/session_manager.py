from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Optional, Dict

from SRC.utils.logger import log
from SRC.managers.account_manager import AccountConfig

_ROOT = Path(__file__).parent.parent.parent
_SESSIONS_DIR = _ROOT / "DATA" / "sessions"
_DEFAULT_TARGET = "https://diablo.trade"
_BASE_CDP_PORT = 9222

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def _find_chrome() -> str:

    for p in _CHROME_PATHS:
        if Path(p).exists():
            return p

    return "chrome"

def _is_port_in_use(port: int) -> bool:

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False

# Manages Chrome browser processes for each account.
# Uses CDP (remote-debugging-port) so Playwright can attach to the real logged-in browser
# instead of launching a fresh guest profile every run.
class SessionManager:

    def __init__(self):
        self._processes: Dict[str, subprocess.Popen] = {}

    @staticmethod
    def session_dir(acct: AccountConfig) -> Path:

        if acct.session_dir:
            d = Path(acct.session_dir)
        else:
            d = _SESSIONS_DIR / acct.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def cdp_port(acct: AccountConfig, index: int = 0) -> int:

        if acct.cdp_port and acct.cdp_port > 0:
            return acct.cdp_port
        return _BASE_CDP_PORT + index

    @staticmethod
    def cdp_url(acct: AccountConfig, index: int = 0) -> str:
        port = SessionManager.cdp_port(acct, index)
        return f"http://localhost:{port}"

    def session_exists(self, acct: AccountConfig) -> bool:

        d = self.session_dir(acct)

        return (d / "Default").is_dir()

    # Launches Chrome in visible mode so the user can log in and save their session cookies.
    def launch_setup(self, acct: AccountConfig, index: int = 0) -> bool:

        if acct.id in self._processes:
            proc = self._processes[acct.id]
            if proc.poll() is None:
                log.warning(f"Browser already running for [{acct.id}] (tracked process)")
                return False

        chrome = _find_chrome()
        port = self.cdp_port(acct, index)
        user_dir = str(self.session_dir(acct))

        if _is_port_in_use(port):
            log.warning(f"Port {port} already in use for [{acct.id}] — skipping launch (close existing browser first)")
            return False

        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_dir}",

            f"--user-agent={_CHROME_UA}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-notifications",
            "--mute-audio",
        ]

        if acct.proxy:
            args.append(f"--proxy-server={acct.proxy}")

        args.append(_DEFAULT_TARGET)

        try:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._processes[acct.id] = proc

            acct.cdp_url = f"http://localhost:{port}"
            if acct.cdp_port == 0:
                acct.cdp_port = port
            if not acct.session_dir:
                acct.session_dir = user_dir
            log.info(f"Launched setup browser for [{acct.id}] on port {port}")
            return True
        except Exception as e:
            log.error(f"Failed to launch Chrome for [{acct.id}]: {e}")
            return False

    # Launches headless Chrome reusing the saved session profile. Skips launch if the
    # CDP port is already in use (assumes an existing browser is still running).
    def launch_headless(self, acct: AccountConfig, index: int = 0) -> bool:

        if acct.id in self._processes:
            proc = self._processes[acct.id]
            if proc.poll() is None:
                log.warning(f"Browser already running for [{acct.id}] (tracked process)")
                return False

        if not self.session_exists(acct):
            log.warning(f"No session data for [{acct.id}]. Run Setup first.")
            return False

        chrome = _find_chrome()
        port = self.cdp_port(acct, index)
        user_dir = str(self.session_dir(acct))

        if _is_port_in_use(port):
            log.warning(f"Port {port} already in use for [{acct.id}] — reusing existing browser")
            acct.cdp_url = f"http://localhost:{port}"
            return True

        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_dir}",
            "--headless=new",
            f"--user-agent={_CHROME_UA}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-notifications",
            "--mute-audio",
        ]

        if acct.proxy:
            args.append(f"--proxy-server={acct.proxy}")

        args.append(_DEFAULT_TARGET)

        try:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._processes[acct.id] = proc
            acct.cdp_url = f"http://localhost:{port}"
            if acct.cdp_port == 0:
                acct.cdp_port = port
            log.info(f"Launched headless browser for [{acct.id}] on port {port}")
            return True
        except Exception as e:
            log.error(f"Failed to launch headless Chrome for [{acct.id}]: {e}")
            return False

    def launch_preview(self, acct: AccountConfig) -> bool:

        chrome = _find_chrome()
        user_dir = str(self.session_dir(acct))

        if not self.session_exists(acct):
            log.warning(
                f"[preview] No session data yet for [{acct.id}] — "
                "browser will open with a fresh (empty) profile"
            )

        args = [
            chrome,
            f"--user-data-dir={user_dir}",

            f"--user-agent={_CHROME_UA}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        if acct.proxy:
            args.append(f"--proxy-server={acct.proxy}")

        args.append(_DEFAULT_TARGET)

        preview_key = f"preview:{acct.id}"

        existing = self._processes.get(preview_key)
        if existing is not None and existing.poll() is None:
            log.warning(f"[preview] Preview browser for [{acct.id}] is already open")
            return False

        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._processes[preview_key] = proc
            log.info(
                f"[preview] Opened preview browser for [{acct.id}] "
                f"(profile: {user_dir})"
            )
            return True
        except Exception as e:
            log.error(f"[preview] Failed to launch Chrome for [{acct.id}]: {e}")
            return False

    def is_preview_running(self, acct_id: str) -> bool:

        proc = self._processes.get(f"preview:{acct_id}")
        return proc is not None and proc.poll() is None

    def kill_browser(self, acct: AccountConfig) -> bool:

        proc = self._processes.pop(acct.id, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.info(f"Killed browser for [{acct.id}]")
            return True
        return False

    def is_browser_running(self, acct_id: str) -> bool:

        proc = self._processes.get(acct_id)
        if proc is not None and proc.poll() is None:
            return True

        return False

    def is_running(self, acct: "AccountConfig", index: int = 0) -> bool:

        proc = self._processes.get(acct.id)
        if proc is not None and proc.poll() is None:
            return True
        port = self.cdp_port(acct, index)
        return _is_port_in_use(port)

    def kill_all(self) -> None:

        for acct_id in list(self._processes.keys()):
            proc = self._processes.pop(acct_id, None)
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        log.info("All managed browsers terminated")
