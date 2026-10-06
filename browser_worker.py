"""
Browser worker layer: the ONE place that starts Playwright, launches the browser with the persistent
profile, creates the page, and shuts everything down.

Scraping code receives a ready `page` (and `context` where needed) and never knows how it was launched.

The browser runs with its own default configuration: this module sets no User-Agent and does not alter
navigator properties, client hints or any other browser value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, Playwright, sync_playwright

# Engine name -> (Playwright browser type, channel). "chrome" = the installed Google Chrome.
ENGINES = {
    "chrome": ("chromium", "chrome"),
    "chromium": ("chromium", None),
    "firefox": ("firefox", None),
    "webkit": ("webkit", None),
}
DEFAULT_ENGINE = "chromium"  # Use Playwright's standalone Chromium by default for guaranteed visible windows
CHROMIUM_FAMILY = {"chrome", "chromium"}


class BrowserLaunchError(Exception):
    """The configured browser could not be started (e.g. its Playwright binary is not installed)."""


def generate_client_hints(user_agent: str) -> dict[str, str]:
    """Generate consistent Sec-CH-UA client hints and Accept-Language for a given Chrome User-Agent."""
    headers = {
        "accept-language": "en-US,en;q=0.9",
    }
    chrome_match = re.search(r"Chrome/(\d+)\.", user_agent)
    version_major = chrome_match.group(1) if chrome_match else "154"

    ua_lower = user_agent.lower()
    if "macintosh" in ua_lower or "mac os x" in ua_lower:
        platform = '"macOS"'
    elif "linux" in ua_lower or "x11" in ua_lower:
        platform = '"Linux"'
    else:
        platform = '"Windows"'

    headers["sec-ch-ua"] = f'"Google Chrome";v="{version_major}", "Chromium";v="{version_major}", "Not A(Brand";v="99"'
    headers["sec-ch-ua-mobile"] = "?0"
    headers["sec-ch-ua-platform"] = platform
    return headers


def parse_proxy(proxy: str | dict | None) -> dict[str, str] | None:
    """
    Parse HTTP/SOCKS5 proxy strings or dicts into Playwright's expected proxy format:
    {"server": "http://ip:port", "username": "...", "password": "..."} or {"server": "socks5://ip:port"}
    Handles usernames with '@' (e.g. email addresses like user@domain.com).
    """
    if not proxy:
        return None
    if isinstance(proxy, dict):
        return proxy
    proxy_str = str(proxy).strip()
    if not proxy_str:
        return None

    scheme = "http"
    rest = proxy_str
    if "://" in proxy_str:
        scheme, rest = proxy_str.split("://", 1)

    if "@" in rest:
        # Split on the LAST '@' so usernames with '@' (emails) are parsed cleanly
        auth, server_host = rest.rsplit("@", 1)
        res = {"server": f"{scheme}://{server_host}"}
        if ":" in auth:
            parts = auth.split(":")
            res["username"] = parts[0]
            res["password"] = ":".join(parts[1:])
        else:
            res["username"] = auth
        return res

    return {"server": f"{scheme}://{rest}"}


@dataclass
class BrowserConfig:
    profile_dir: Path
    engine: str = DEFAULT_ENGINE
    headless: bool = False
    # The default profile is a Chrome-format profile (it holds the manual Uber login). Firefox/WebKit
    # must never open it; for those engines an explicit, different profile_dir is required.
    default_profile_dir: Path | None = None
    user_agent: str | None = None
    viewport: dict[str, int] | None = None
    extra_http_headers: dict[str, str] | None = None
    proxy: str | dict | None = None


DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"


class PlaywrightWorker:
    """
    Usage:
        with PlaywrightWorker(config) as worker:
            run_something(worker.page)
    """

    def __init__(self, config: BrowserConfig):
        if config.engine not in ENGINES:
            raise ValueError(f"Unknown browser engine {config.engine!r}; choose from {sorted(ENGINES)}")
        self.config = config
        self.engine_used: str | None = None  # may differ from config.engine only via the chrome->chromium fallback
        self._playwright_cm = None
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    # -- lifecycle ---------------------------------------------------------------------------------

    def start(self) -> Page:
        if self.context is not None:
            raise RuntimeError("PlaywrightWorker is already started")
        self.check_profile()
        self._playwright_cm = sync_playwright()
        self.playwright = self._playwright_cm.__enter__()
        try:
            self.context = self._launch()
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        except PlaywrightError as exc:
            self.close()
            first = exc.message.splitlines()[0]
            hint = ""
            if "Executable doesn't exist" in exc.message:
                kind = ENGINES[self.config.engine][0]
                hint = f" Install it with: python -m playwright install {kind}"
            raise BrowserLaunchError(f"Could not start {self.config.engine}: {first}.{hint}") from None
        except BaseException:
            self.close()
            raise
        return self.page

    def close(self) -> None:
        """Close page -> context (with a persistent profile this is also the browser) -> Playwright."""
        if self.page is not None:
            try:
                if not self.page.is_closed():
                    self.page.close()
            except PlaywrightError:
                pass
            self.page = None
        if self.context is not None:
            try:
                self.context.close()
            except PlaywrightError:
                pass  # already closed (e.g. the user closed the window)
            self.context = None
        if self._playwright_cm is not None:
            try:
                self._playwright_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._playwright_cm = None
            self.playwright = None

    def __enter__(self) -> "PlaywrightWorker":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- launch --------------------------------------------------------------------------------------

    def check_profile(self) -> None:
        cfg = self.config
        uses_default = cfg.default_profile_dir is not None and Path(cfg.profile_dir).resolve() == Path(
            cfg.default_profile_dir).resolve()
        if cfg.engine not in CHROMIUM_FAMILY and uses_default:
            raise ValueError(
                f"The default profile ({cfg.profile_dir}) is a Chrome profile holding the Uber login; "
                f"{cfg.engine} must not open it. Pass --profile-dir with a separate folder for {cfg.engine} "
                "(you would then log in there manually with `open`).")
        if not Path(cfg.profile_dir).exists() and not uses_default:
            raise ValueError(f"Profile folder {cfg.profile_dir} does not exist. Create it yourself if you "
                             "intend to start a new profile (it is never created automatically).")

    def _launch(self) -> BrowserContext:
        cfg = self.config
        options = dict(
            user_data_dir=str(cfg.profile_dir),
            headless=cfg.headless,
        )
        if cfg.viewport is not None:
            options["viewport"] = cfg.viewport
        else:
            options["no_viewport"] = True

        ua_to_use = cfg.user_agent or DEFAULT_USER_AGENT
        options["user_agent"] = ua_to_use
        headers = generate_client_hints(ua_to_use)
        if cfg.extra_http_headers:
            headers.update(cfg.extra_http_headers)
        options["extra_http_headers"] = headers

        if cfg.proxy:
            parsed_proxy = parse_proxy(cfg.proxy)
            if parsed_proxy:
                options["proxy"] = parsed_proxy

        if cfg.engine in CHROMIUM_FAMILY:
            args = [
                "--start-maximized",
                "--window-position=0,0",
                "--window-size=1400,900",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            options["args"] = args

        browser_type_name, channel = ENGINES[cfg.engine]
        browser_type = getattr(self.playwright, browser_type_name)
        if channel:
            try:
                context = browser_type.launch_persistent_context(channel=channel, **options)
                context.set_extra_http_headers(headers)
                self.engine_used = cfg.engine
                return context
            except PlaywrightError as exc:
                print(f"[warn] Could not launch installed Google Chrome ({exc.message.splitlines()[0]}); "
                      "falling back to Playwright Chromium.")
                self.engine_used = "chromium"
                context = browser_type.launch_persistent_context(**options)
                context.set_extra_http_headers(headers)
                return context
        self.engine_used = cfg.engine
        context = browser_type.launch_persistent_context(**options)
        context.set_extra_http_headers(headers)
        return context

    # -- diagnostics (read-only) -------------------------------------------------------------------

    def browser_executable(self) -> str | None:
        """Path of the running browser binary (first non-Python/non-driver process we started)."""
        try:
            import psutil
        except ImportError:
            return None
        try:
            for proc in psutil.Process(os.getpid()).children(recursive=True):
                name = proc.name().lower()
                if not any(skip in name for skip in ("python", "node", "conhost", "cmd")):
                    return proc.exe()
        except psutil.Error:
            pass
        return None

    def browser_pid(self) -> int | None:
        """PID of the running browser process."""
        try:
            import psutil
        except ImportError:
            return None
        try:
            for proc in psutil.Process(os.getpid()).children(recursive=True):
                name = proc.name().lower()
                if not any(skip in name for skip in ("python", "node", "conhost", "cmd")):
                    return proc.pid
        except psutil.Error:
            pass
        return None

    @staticmethod
    def playwright_version() -> str | None:
        try:
            return package_version("playwright")
        except PackageNotFoundError:
            return None
