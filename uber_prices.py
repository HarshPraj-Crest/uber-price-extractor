"""
Uber price extractor (read-only proof of concept).

Opens Uber in a visible browser using a persistent profile so a manual login
is remembered between runs. This tool NEVER requests or books a ride.

Current stage: sequential batch extraction (one browser, many routes).

Usage:
    python uber_prices.py open
        Open Uber in a visible browser. Log in manually if needed, then press
        Enter in this terminal to close the browser and save the session.

    python uber_prices.py check-session
        Reopen the saved profile and verify Uber still shows it as logged in.

    python uber_prices.py locations --pickup "..." --destination "..."
        Enter both locations, show Uber's suggestions, select only an exact
        match, and verify what Uber actually selected. Location syntax:
            "Surat Railway Station"            -> suggestion name must equal this
            "Surat Railway Station, Varachha"  -> ...and its address must contain "Varachha"
        If there is no exact match, or more than one, it stops and lists the choices.

    python uber_prices.py search --pickup "..." --destination "..." [--close]
        Same as `locations`, then clicks Search and waits until Uber shows ride
        options with prices. The browser stays open for visual checking.

    python uber_prices.py prices --pickup "..." --destination "..." [--keep-open]
        One route: select + verify, Search, extract and validate prices, save JSON.

    python uber_prices.py batch [--input data/routes.csv] [--output data/results.csv] [--only 1,2,18]
        Routes listed in the input CSV (route_id,source,destination[,category]), sequentially, in ONE
        browser. Source/destination labels are mapped to exact Uber places via routes.json "locations".
        Appends one row per ride (or one status row per failed route) to the output CSV, and still
        writes data/results/batch-<ts>.json and batch-<ts>-performance.json.
        Multi-stop routes are skipped (not implemented). Stops on AUTH_REQUIRED.

    python uber_prices.py browser-info
        Read-only: show engine, executable, versions, User-Agent, navigator values, profile in use.

Browser options (any command, before or after the command name):
    --browser chrome|chromium|firefox|webkit   default: chrome (installed Google Chrome)
    --profile-dir PATH                         default: browser_profile/ (holds the Uber login)
    --chromium                                 legacy alias for --browser chromium
Browser startup lives in browser_worker.py (PlaywrightWorker).

    python uber_prices.py prices --pickup "..." --destination "..."
        (Not implemented yet -- later steps.)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

# --- Accounts & Human Behavior Configuration ---------------------------------------------------
ACCOUNTS_CSV = BASE_DIR / "data" / "accounts.csv" if "BASE_DIR" in locals() else Path(__file__).resolve().parent / "data" / "accounts.csv"

REALISTIC_USER_AGENTS = [
    # Windows Chrome 131 & 130
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # macOS Chrome 131 & 130
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Linux Chrome 131 & 130
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

DEFAULT_ACCOUNTS = [
    {"account_name": "Account_1", "phone_number": "+10000000001", "profile_dir": "browser_profile_account1", "status": "active", "last_used": "", "user_agent": REALISTIC_USER_AGENTS[0], "notes": "Primary account (Win Chrome 131)"},
    {"account_name": "Account_2", "phone_number": "+10000000002", "profile_dir": "browser_profile_account2", "status": "active", "last_used": "", "user_agent": REALISTIC_USER_AGENTS[2], "notes": "Secondary account (Mac Chrome 131)"},
    {"account_name": "Account_3", "phone_number": "+10000000003", "profile_dir": "browser_profile_account3", "status": "active", "last_used": "", "user_agent": REALISTIC_USER_AGENTS[4], "notes": "Backup account 1 (Linux Chrome 131)"},
    {"account_name": "Account_4", "phone_number": "+10000000004", "profile_dir": "browser_profile_account4", "status": "active", "last_used": "", "user_agent": REALISTIC_USER_AGENTS[1], "notes": "Backup account 2 (Win Chrome 130)"},
]


def human_pause(min_sec: float = 0.5, max_sec: float = 1.5, scale: float = 1.0, enabled: bool = True) -> float:
    """Simulate human reaction time and pauses to avoid bot detection."""
    if not enabled or scale <= 0:
        return 0.0
    duration = random.uniform(min_sec, max_sec) * scale
    time.sleep(duration)
    return duration


def load_accounts(csv_path: Path | None = None) -> list[dict]:
    path = csv_path or ACCOUNTS_CSV
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        save_accounts(DEFAULT_ACCOUNTS, path)
        return [dict(a) for a in DEFAULT_ACCOUNTS]
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        accounts = [row for row in reader if row.get("account_name")]
    if not accounts:
        save_accounts(DEFAULT_ACCOUNTS, path)
        return [dict(a) for a in DEFAULT_ACCOUNTS]
    
    # Auto-assign realistic User-Agent to existing accounts if missing
    updated = False
    for acc in accounts:
        if not acc.get("user_agent"):
            acc["user_agent"] = random.choice(REALISTIC_USER_AGENTS)
            updated = True
    if updated:
        save_accounts(accounts, path)

    return accounts


def save_accounts(accounts: list[dict], csv_path: Path | None = None) -> None:
    path = csv_path or ACCOUNTS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["account_name", "phone_number", "profile_dir", "status", "last_used", "user_agent", "notes"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for acc in accounts:
            writer.writerow({k: acc.get(k, "") for k in fieldnames})


def get_account(identifier: str, csv_path: Path | None = None) -> dict | None:
    accounts = load_accounts(csv_path)
    ident_clean = identifier.strip().lower()
    for acc in accounts:
        if acc.get("account_name", "").strip().lower() == ident_clean or acc.get("phone_number", "").strip().lower() == ident_clean:
            return acc
    return None


def update_account_status(identifier: str, status: str, notes: str | None = None, csv_path: Path | None = None) -> dict | None:
    accounts = load_accounts(csv_path)
    target = None
    ident_clean = identifier.strip().lower()
    for acc in accounts:
        if acc.get("account_name", "").strip().lower() == ident_clean or acc.get("phone_number", "").strip().lower() == ident_clean:
            acc["status"] = status
            acc["last_used"] = datetime.now().isoformat(timespec="seconds")
            if notes is not None:
                acc["notes"] = notes
            target = acc
            break
    if target:
        save_accounts(accounts, csv_path)
    return target


def get_next_active_account(current_account_name: str | None = None, csv_path: Path | None = None) -> dict | None:
    accounts = load_accounts(csv_path)
    active = [a for a in accounts if a.get("status") == "active"]
    if not active:
        return None
    if not current_account_name:
        return active[0]
    names = [a.get("account_name") for a in active]
    if current_account_name in names:
        idx = names.index(current_account_name)
        return active[(idx + 1) % len(active)]
    return active[0]


from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from browser_worker import DEFAULT_ENGINE, ENGINES, BrowserConfig, BrowserLaunchError, PlaywrightWorker

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "browser_profile"
RESULTS_DIR = BASE_DIR / "data" / "results"
SCREENSHOTS_DIR = BASE_DIR / "data" / "screenshots"

UBER_HOME_URL = "https://www.uber.com/"
# Rider booking page; requires login (logged-out users are sent to auth.uber.com).
UBER_BOOKING_URL = "https://m.uber.com/go/home"

NAV_TIMEOUT_MS = 60_000

# Uber pages contain non-ASCII text (e.g. the rupee sign); Windows consoles default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Visible text that suggests Uber is showing a security challenge instead of the normal page.
CHALLENGE_MARKERS = ("captcha", "verify you are human", "unusual activity", "security check")


class ExtractorError(Exception):
    """A failure with a clear, user-facing reason. Never swallowed into a fake success."""


class LoginRequiredError(ExtractorError):
    pass


class SecurityChallengeError(ExtractorError):
    pass


class LocationNotFoundError(ExtractorError):
    pass


class AmbiguousLocationError(ExtractorError):
    pass


class WrongLocationSelectedError(ExtractorError):
    pass


class SearchUnavailableError(ExtractorError):
    """Uber returned no suggestion list at all for the typed text (not the same as an ambiguous place)."""


# Last Uber location-search response per page (diagnostics for SearchUnavailableError).
_last_search_response: dict[int, str] = {}


def attach_search_monitor(page: Page) -> None:
    """Remember the last PudoLocationSearch response so a search failure can say what Uber answered."""
    def on_response(resp):
        if "/go/graphql" not in resp.url:
            return
        try:
            op = (resp.request.post_data_json or {}).get("operationName")
        except Exception:
            return
        if op == "PudoLocationSearch":
            _last_search_response[id(page)] = f"HTTP {resp.status} at {datetime.now():%H:%M:%S}"
    page.on("response", on_response)


# --- Location selection -------------------------------------------------------------------------

# Uber's autocomplete: each result is li[role=option][data-testid=pudo-result] with <p>name</p><p>address</p>.
SUGGESTION_SELECTOR = '[role="listbox"] li[role="option"][data-testid="pudo-result"]'
# The pickup / dropoff fields are identical comboboxes; they are told apart by the icon in their container.
FIELD_ICON_TESTID = {"pickup": "pickup-icon", "destination": "drop-icon"}
# Page-URL query parameter where Uber stores the selected place as JSON (drop is a list: drop[0], drop[1]...).
URL_PARAM = {"pickup": "pickup", "destination": "drop[0]"}
UTILITY_SUGGESTIONS = ("set location on map", "search in a different city", "see more results for")


@dataclass
class Suggestion:
    index: int  # position in Uber's list (0-based), used only to click the chosen element
    name: str
    address: str

    def __str__(self) -> str:
        return f"{self.name} -- {self.address}" if self.address else self.name


@dataclass
class SelectedLocation:
    name: str
    address: str
    latitude: float | None
    longitude: float | None
    place_id: str | None
    provider: str | None


def normalize(text: str) -> str:
    """Lowercase, REMOVE punctuation, collapse spaces: "Tootsie's" -> "tootsies", "A & B" -> "a b".
    Uber stores some names with punctuation stripped ("Victory Restaurant  Lounge", "Tootsies Cabaret Miami")."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


@dataclass
class LocationSpec:
    """
    What to select: a suggestion whose name exactly equals `name` (or one of `aliases` -- Uber shows Google
    names in the pickup field and its own names in the destination field, e.g. "LIV Nightclub Miami" vs
    "Liv Nightclub"), plus words that must appear (as whole words) in its address.
    CLI syntax: "Name, hint1, hint2". In routes.json: {"name": ..., "aliases": [...], "address": [...]}.
    `name` is what gets typed into Uber.
    """
    name: str
    address_hints: list[str]
    label: str | None = None  # human label from the route list, for reports
    aliases: tuple[str, ...] = ()

    @classmethod
    def parse(cls, query: str) -> "LocationSpec":
        name, *hints = [part.strip() for part in query.split(",")]
        return cls(name=name, address_hints=[h for h in hints if h])

    @classmethod
    def from_config(cls, cfg: dict) -> "LocationSpec":
        return cls(name=cfg["name"], address_hints=list(cfg.get("address", [])), label=cfg.get("label"),
                   aliases=tuple(cfg.get("aliases", [])))

    def matches(self, s: Suggestion) -> bool:
        address = f" {normalize(s.address)} "
        return normalize(s.name) in {normalize(n) for n in (self.name, *self.aliases)} and all(
            f" {normalize(h)} " in address for h in self.address_hints)

    def __str__(self) -> str:
        names = " / ".join((self.name, *self.aliases))
        return names + (f" [address: {', '.join(self.address_hints)}]" if self.address_hints else "")


def match_suggestion(spec: LocationSpec, suggestions: list[Suggestion]) -> Suggestion:
    """
    Pick the single suggestion that exactly matches the requested location.
    Never falls back to the first / closest result: no match or several matches is an error.
    """
    matches = [s for s in suggestions if spec.matches(s)]
    choices = "\n".join(f"    [{s.index}] {s}" for s in suggestions) or "    (none)"
    if not matches:
        raise LocationNotFoundError(
            f"No Uber suggestion exactly matches {str(spec)!r}. Nothing was selected.\n"
            f"  Suggestions Uber showed:\n{choices}\n"
            "  Use the exact name of the correct place (and address words to pin it).")
    if len({(normalize(s.name), normalize(s.address)) for s in matches}) > 1:
        listed = "\n".join(f"    [{s.index}] {s}" for s in matches)
        raise AmbiguousLocationError(
            f"{str(spec)!r} matches several different places. Nothing was selected.\n{listed}\n"
            "  Add address words to pin one, e.g. \"<name>, <street or area>\".")
    return matches[0]


def location_field(page: Page, role: str):
    container = page.locator('[data-testid="pudo-select-v2"]').filter(
        has=page.locator(f'[data-testid="{FIELD_ICON_TESTID[role]}"]'))
    return container.get_by_role("combobox")


def read_suggestions(page: Page, query: str) -> list[Suggestion]:
    """Wait for the suggestion list for *this* query and return the real place results (utility rows removed)."""
    # Uber appends a "See more results for <query>" row, which proves the list belongs to the current text.
    # Without it the visible list may be stale (a previous query's results), so it must not be used.
    try:
        page.locator(SUGGESTION_SELECTOR).filter(has_text=f"See more results for {query}").first.wait_for(
            state="visible", timeout=20_000)
    except PlaywrightError:
        shot = save_screenshot(page, "no-suggestions")
        values = [c.input_value() for c in page.get_by_role("combobox", name="Search for a location").all()]
        last = _last_search_response.get(id(page), "none seen / not monitored")
        raise SearchUnavailableError(
            f"Uber returned no suggestion list for {query!r} (any visible list may belong to an earlier query). "
            f"Field values at failure: {values}; last location-search response: {last}. Screenshot: {shot}")
    page.wait_for_timeout(1_000)  # let late results render

    suggestions = []
    for i, item in enumerate(page.locator(SUGGESTION_SELECTOR).all()):
        lines = [t.strip() for t in item.locator("p").all_inner_texts() if t.strip()]
        if not lines or lines[0].lower().startswith(UTILITY_SUGGESTIONS):
            continue
        suggestions.append(Suggestion(index=i, name=lines[0], address=lines[1] if len(lines) > 1 else ""))
    return suggestions


def place_json_from_url(page: Page, role: str) -> str | None:
    """The raw JSON Uber stores in the URL for the selected place (exactly as Uber produced it)."""
    raw = parse_qs(urlparse(page.url).query).get(URL_PARAM[role])
    return raw[0] if raw else None


def selected_location_from_url(page: Page, role: str) -> SelectedLocation | None:
    raw = parse_qs(urlparse(page.url).query).get(URL_PARAM[role])
    if not raw:
        return None
    try:
        data = json.loads(raw[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return SelectedLocation(
        name=data.get("addressLine1", ""), address=data.get("addressLine2", ""),
        latitude=data.get("latitude"), longitude=data.get("longitude"),
        place_id=data.get("id"), provider=data.get("provider"))


def select_location(page: Page, role: str, spec: LocationSpec | str, verbose: bool = True,
                    timings: dict | None = None, human_delays: bool = True,
                    delay_scale: float = 1.0) -> tuple[list[Suggestion], Suggestion, SelectedLocation]:
    """Type the name, list Uber's suggestions, click only the exact match, and verify Uber's selection."""
    if isinstance(spec, str):
        spec = LocationSpec.parse(spec)
    timings = {} if timings is None else timings
    t = time.perf_counter()

    def lap(key: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[key] = round(timings.get(key, 0) + now - t, 2)
        t = now

    field = location_field(page, role)
    try:
        field.wait_for(state="visible", timeout=20_000)
    except PlaywrightError:
        shot = save_screenshot(page, f"{role}-field-missing")
        raise ExtractorError(f"Could not find the {role} input on the page. Screenshot: {shot}")
    lap("field_ready")

    # Slight human reaction pause before clicking input
    human_pause(0.3, 0.7, scale=delay_scale, enabled=human_delays)

    def type_name() -> None:
        field.click()
        human_pause(0.2, 0.4, scale=delay_scale, enabled=human_delays)
        field.fill("")
        if human_delays and delay_scale > 0:
            for char in spec.name:
                field.press(char)
                char_delay = random.uniform(0.06, 0.16) * delay_scale
                if char in " ,.-":
                    char_delay += random.uniform(0.1, 0.25) * delay_scale
                time.sleep(char_delay)
        else:
            field.press_sequentially(spec.name, delay=60)
        lap("typing")

    # Retry typing ONCE, and only if the field actually lost the typed text (seen 2026-09-30: empty field
    # with Uber's default list). If the text is still there and Uber gives no list, do not retry.
    retried = False
    type_name()
    if field.input_value() != spec.name:
        retried = True
        timings["typing_retries"] = timings.get("typing_retries", 0) + 1
        type_name()
    try:
        suggestions = read_suggestions(page, spec.name)
    except SearchUnavailableError:
        lap("suggestions")
        if retried or field.input_value() == spec.name:
            raise
        timings["typing_retries"] = timings.get("typing_retries", 0) + 1
        type_name()
        suggestions = read_suggestions(page, spec.name)
    lap("suggestions")
    if verbose:
        print(f"\nUber suggestions for {role} {str(spec)!r}:")
        for s in suggestions:
            print(f"  [{s.index}] {s}")

    try:
        chosen = match_suggestion(spec, suggestions)
    except ExtractorError:
        save_screenshot(page, f"{role}-no-unique-match")
        raise
    if verbose:
        print(f"  -> exact match: [{chosen.index}] {chosen}")

    # Human pause to visually confirm match before clicking
    human_pause(0.5, 1.2, scale=delay_scale, enabled=human_delays)

    before = page.url
    page.locator(SUGGESTION_SELECTOR).nth(chosen.index).click()
    try:
        # Uber updates the URL client-side; "commit" avoids waiting for a full page-load event that the
        # live map delays by several seconds (measured: up to the whole 15s timeout per selection).
        page.wait_for_url(lambda url: url != before and URL_PARAM[role] in parse_qs(urlparse(url).query),
                          timeout=15_000, wait_until="commit")
    except PlaywrightError:
        pass
    lap("click_and_confirm")
    selected = selected_location_from_url(page, role)
    if selected is None:
        shot = save_screenshot(page, f"{role}-not-confirmed")
        raise WrongLocationSelectedError(
            f"Clicked {chosen.name!r} but could not confirm Uber's {role} selection. Screenshot: {shot}")
    # Uber may store the name without a trailing code shown in the suggestion:
    # "Miami International Airport (MIA)" -> "Miami International Airport".
    accepted = {normalize(chosen.name), normalize(re.sub(r"\s*\([^)]*\)\s*$", "", chosen.name))}
    name_ok = normalize(selected.name) in accepted
    # ...or with ", <part of the clicked suggestion's own address>" appended:
    # clicked "The Setai" (address "Collins Avenue, Miami Beach, FL, USA") -> stored "The Setai, Miami Beach".
    # The part before the last comma must be an accepted name, and the appended part must appear as whole
    # words in that suggestion's address. Nothing else may differ.
    if not name_ok and "," in selected.name:
        head, suffix = selected.name.rsplit(",", 1)
        name_ok = (normalize(head) in accepted and normalize(suffix) != ""
                   and f" {normalize(suffix)} " in f" {normalize(chosen.address)} ")
    if not name_ok:
        shot = save_screenshot(page, f"{role}-wrong-location")
        raise WrongLocationSelectedError(
            f"Uber selected {selected.name!r} for {role}, expected {chosen.name!r}. Screenshot: {shot}")
    return suggestions, chosen, selected


def select_route(page: Page, pickup_query: LocationSpec | str,
                 destination_query: LocationSpec | str) -> tuple[SelectedLocation, SelectedLocation]:
    """Select and verify pickup then destination; confirm the pickup survived the second selection."""
    _, _, pickup = select_location(page, "pickup", pickup_query)
    _, _, destination = select_location(page, "destination", destination_query)
    verify_route_in_url(page, pickup, destination)
    return pickup, destination


def verify_route_in_url(page: Page, pickup: SelectedLocation, destination: SelectedLocation) -> None:
    """Uber keeps the selected places in the URL; make sure both are still exactly the ones we verified."""
    for role, expected in (("pickup", pickup), ("destination", destination)):
        current = selected_location_from_url(page, role)
        if current is None or current.place_id != expected.place_id:
            shot = save_screenshot(page, f"{role}-changed")
            found = current.name if current else "nothing"
            raise WrongLocationSelectedError(
                f"Uber's {role} is now {found!r}, expected {expected.name!r} ({expected.place_id}). Screenshot: {shot}")


# --- Search -> ride options ---------------------------------------------------------------------

# Words that must never appear on anything this tool clicks (read-only safety guard).
FORBIDDEN_CLICK_WORDS = ("request", "confirm", "book", "reserve", "schedule", "pay")
# Ride-option rows on /go/product-selection: ul[role=listbox] > li[role=option][data-testid=product_selector.list_item]
RIDE_OPTION_SELECTOR = 'li[role="option"][data-testid="product_selector.list_item"]'
# A price-like token: currency symbol or code followed by digits (e.g. "₹342.49", "$10.93", "INR 120").
PRICE_PATTERN = re.compile(r"(?:[₹$€£]|\b[A-Z]{3}\s?)\s?\d[\d,]*(?:\.\d+)?")
# Measured: some routes (e.g. Miami -> West Palm Beach) still show loading placeholders after 20s.
RESULTS_TIMEOUT_MS = 45_000


class RideOptionsNotLoadedError(ExtractorError):
    pass


def safe_click(locator, what: str) -> None:
    """Click only if the element's visible text / accessible name has no booking-related words."""
    label = " ".join(filter(None, [locator.inner_text(), locator.get_attribute("aria-label")])).lower()
    if any(word in label for word in FORBIDDEN_CLICK_WORDS):
        raise ExtractorError(f"Refusing to click {what}: its label {label!r} looks like a booking action.")
    locator.click()


def find_search_button(page: Page):
    """
    The route "Search" button, identified from the live page (2026-09-29):
    <button data-baseweb="button" aria-label="Search">Search</button> in the "Get a ride" panel,
    enabled once pickup and drop are set; not inside a <form> (JS click handler).
    """
    button = page.get_by_role("button", name="Search", exact=True)
    count = button.count()
    if count != 1:
        shot = save_screenshot(page, "search-button-not-unique")
        raise ExtractorError(f"Expected exactly one 'Search' button, found {count}. Screenshot: {shot}")
    if not button.is_visible() or not button.is_enabled():
        shot = save_screenshot(page, "search-button-disabled")
        raise ExtractorError(f"'Search' button is not clickable (visible={button.is_visible()}, "
                             f"enabled={button.is_enabled()}). Screenshot: {shot}")
    return button


def page_summary(page: Page) -> str:
    """What the page is showing, for failure reports (headings + first lines of the main panel)."""
    try:
        headings = [h.strip() for h in page.get_by_role("heading").all_inner_texts() if h.strip()]
        main = page.locator("main").first if page.locator("main").count() else page.locator("body")
        lines = [l.strip() for l in main.inner_text().splitlines() if l.strip()][:15]
    except PlaywrightError:
        return f"url={page.url}"
    return f"url={urlparse(page.url).path}\n  headings: {headings}\n  text: {' | '.join(lines)}"


def search_rides(page: Page, pickup: SelectedLocation, destination: SelectedLocation,
                 screenshot_before: bool = True, human_delays: bool = True,
                 delay_scale: float = 1.0) -> int:
    """Click Search and wait until Uber actually shows priced ride options. Returns the number of priced rows."""
    button = find_search_button(page)
    if screenshot_before:
        before = save_screenshot(page, "before-search")
        print(f"\n[info] Clicking 'Search' (screenshot before: {before})")
    human_pause(0.8, 1.8, scale=delay_scale, enabled=human_delays)
    safe_click(button, "the Search button")
    return wait_for_ride_options(page, pickup, destination)


def wait_for_ride_options(page: Page, pickup: SelectedLocation, destination: SelectedLocation) -> int:
    """Wait until /go/product-selection shows ride rows with prices, for exactly this route."""
    try:
        page.wait_for_url("**/go/product-selection**", timeout=RESULTS_TIMEOUT_MS, wait_until="commit")
    except PlaywrightError:
        shot = save_screenshot(page, "results-no-navigation")
        raise RideOptionsNotLoadedError(
            f"Search did not open the ride options page.\n  {page_summary(page)}\n  Screenshot: {shot}")

    # The URL change alone is not success: wait until ride rows with a visible price are rendered.
    try:
        count = wait_for_stable_priced_rows(page)
    except PlaywrightError:
        shot = save_screenshot(page, "results-no-prices")
        raise RideOptionsNotLoadedError(
            f"Ride options page opened but no stable list of priced rides appeared.\n"
            f"  {page_summary(page)}\n  Screenshot: {shot}")
    verify_route_in_url(page, pickup, destination)
    return count


STABLE_READ_INTERVAL_MS = 1_000
STABLE_MAX_READS = 12


def wait_for_stable_priced_rows(page: Page) -> int:
    """
    Wait for priced ride rows, then until two consecutive reads ~1s apart give the same non-zero count.
    Measured 2026-09-30: Uber sometimes re-fetches prices and briefly replaces the list with loading
    placeholders, so a single "first priced row is visible" check was not enough.
    """
    priced = page.locator(RIDE_OPTION_SELECTOR).filter(has_text=PRICE_PATTERN)
    priced.first.wait_for(state="visible", timeout=RESULTS_TIMEOUT_MS)
    previous = priced.count()
    for _ in range(STABLE_MAX_READS):
        page.wait_for_timeout(STABLE_READ_INTERVAL_MS)
        current = priced.count()
        if current and current == previous:
            return current
        if not current:  # list was replaced by placeholders: wait for priced rows to come back
            priced.first.wait_for(state="visible", timeout=RESULTS_TIMEOUT_MS)
            current = priced.count()
        previous = current
    raise PriceNotFoundError(f"The priced ride list never settled (last count {previous}).")


def extract_rides_stable(page: Page) -> tuple[list["RideOption"], int]:
    """Extract from a settled list; if nothing is found, allow ONE more wait before giving up."""
    count = page.locator(RIDE_OPTION_SELECTOR).filter(has_text=PRICE_PATTERN).count()
    rides = extract_rides(page)
    if rides:
        return rides, count
    try:
        count = wait_for_stable_priced_rows(page)
    except PlaywrightError:
        raise PriceNotFoundError("No priced ride rows after an additional wait.")
    return extract_rides(page), count


# --- Price extraction ---------------------------------------------------------------------------

CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}
# A whole element's text that is exactly one amount: "₹342.25", "$12.99", "US$12.99", "INR 120".
FULL_PRICE_PATTERN = re.compile(r"^(?:[A-Z]{0,3}[₹$€£]|[A-Z]{3}\s)\s?\d[\d,]*(?:\.\d+)?$")

# Ride row DOM (inspected live 2026-09-30):
#   <li role=option data-testid="product_selector.list_item">
#     <p>Uber Go<span></span><span>4</span></p>                       <- name + seat-count span
#     <p data-testid="product_selector.list_item.eta_string">1 min away • 11:43 AM</p>
#     <div>Faster</div> / tagline
#     <p>₹345.36</p>  (24px bold)   <p>₹383.73</p>  (line-through = original price, optional)
# Everything is read from inside one row element, so a price can never be paired with another row's name.
ROW_DATA_JS = """
(rows) => rows.map(row => {
  const eta = row.querySelector('[data-testid="product_selector.list_item.eta_string"]');
  const nameEl = eta && eta.previousElementSibling;
  // own text nodes only: excludes the seat-count <span>
  const name = nameEl ? [...nameEl.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() : '';
  const prices = [...row.querySelectorAll('p, span, div')]
    .filter(e => e.children.length === 0 && e.textContent.trim() && !e.closest('[aria-hidden="true"]'))
    .map(e => {
      let dec = getComputedStyle(e).textDecorationLine, m = e;
      while (!dec.includes('line-through') && m !== row) { m = m.parentElement; dec = getComputedStyle(m).textDecorationLine; }
      return {text: e.textContent.trim(), strike: dec.includes('line-through') || !!e.closest('s,del,strike')};
    });
  return {name, leaves: prices};
})
"""


@dataclass
class RideOption:
    ride_type: str
    price: str
    original_price: str | None
    currency: str | None
    row_text: str  # raw row text, kept for validation/debugging only


class PriceNotFoundError(ExtractorError):
    pass


def currency_of(price: str) -> str | None:
    code = re.match(r"([A-Z]{3})\s", price)
    if code:
        return code.group(1)
    symbol = re.search(r"[₹$€£]", price)
    if not symbol:
        return None
    prefix = price[:symbol.start()]
    if symbol.group() == "$" and prefix and prefix != "US":
        return None  # e.g. CA$ / A$ -- unknown, don't guess
    return CURRENCY_SYMBOLS[symbol.group()]


def parse_ride_row(name: str, leaves: list[dict], row_text: str) -> RideOption:
    prices = [l for l in leaves if FULL_PRICE_PATTERN.match(l["text"])]
    current = [l["text"] for l in prices if not l["strike"]]
    struck = [l["text"] for l in prices if l["strike"]]
    if not name or not current:
        raise PriceNotFoundError(f"Could not read name/price from ride row {row_text!r}")
    if len(current) > 1 or len(struck) > 1:
        raise PriceNotFoundError(f"Ride row has several candidate prices {current + struck}: {row_text!r}")
    return RideOption(ride_type=name, price=current[0], original_price=struck[0] if struck else None,
                      currency=currency_of(current[0]), row_text=row_text)


def extract_rides(page: Page) -> list[RideOption]:
    """Read every ride-option row on /go/product-selection. Read-only: nothing is clicked."""
    rows = page.locator(RIDE_OPTION_SELECTOR)
    row_texts = [" | ".join(l.strip() for l in t.splitlines() if l.strip()) for t in rows.all_inner_texts()]
    row_data = rows.evaluate_all(ROW_DATA_JS)
    rides, skipped = [], []
    for data, text in zip(row_data, row_texts):
        if not PRICE_PATTERN.search(text):
            skipped.append(text)  # e.g. an unavailable ride type with no price
            continue
        rides.append(parse_ride_row(data["name"], data["leaves"], text))
    if skipped:
        print(f"[info] {len(skipped)} ride row(s) without a price were skipped: {skipped}")
    return rides


def validate_rides(rides: list[RideOption], expected_priced_rows: int) -> None:
    if not rides:
        raise PriceNotFoundError("No ride prices were extracted.")
    if len(rides) != expected_priced_rows:
        raise PriceNotFoundError(f"Extracted {len(rides)} rides but the page shows {expected_priced_rows} priced rows.")
    names = [r.ride_type for r in rides]
    if len(set(names)) != len(names):
        raise PriceNotFoundError(f"Duplicate ride names extracted: {names}")
    for r in rides:
        # Same-row check: the name and price must both come from the one row's own text.
        if r.ride_type not in r.row_text or r.price not in r.row_text:
            raise PriceNotFoundError(f"Name/price mismatch for {r.ride_type!r} in row {r.row_text!r}")
        if r.original_price and r.original_price not in r.row_text:
            raise PriceNotFoundError(f"Original price mismatch for {r.ride_type!r} in row {r.row_text!r}")
    currencies = {r.currency for r in rides}
    if len(currencies) != 1 or None in currencies:
        raise PriceNotFoundError(f"Could not determine one currency for all rides: {currencies}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def amount_of(price: str | None) -> float | None:
    """'₹1,234.50' -> 1234.5 (numeric value for storage; the displayed string is kept as-is too)."""
    if not price:
        return None
    number = re.search(r"\d[\d,]*(?:\.\d+)?", price)
    return float(number.group(0).replace(",", "")) if number else None


def route_result(pickup: SelectedLocation, destination: SelectedLocation, rides: list[RideOption],
                 fetched_at: datetime) -> dict:
    return {
        "pickup": pickup.name,
        "destination": destination.name,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "currency": rides[0].currency,
        "rides": [{"ride_type": r.ride_type, "price": r.price, "original_price": r.original_price,
                   "price_value": amount_of(r.price), "original_price_value": amount_of(r.original_price)}
                  for r in rides],
        # Exact places Uber used, so later consumers (e.g. the Laravel backend) can verify the route.
        "pickup_details": pickup.__dict__,
        "destination_details": destination.__dict__,
    }


def save_result(pickup: SelectedLocation, destination: SelectedLocation, rides: list[RideOption],
                fetched_at: datetime) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result = route_result(pickup, destination, rides, fetched_at)
    path = RESULTS_DIR / f"{fetched_at:%Y%m%d-%H%M%S}_{slug(pickup.name)}_to_{slug(destination.name)}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def keep_browser_open(page: Page) -> None:
    """Block until the user presses Enter here or closes the browser window."""
    import threading

    pressed = threading.Event()

    def wait_for_enter():
        try:
            input()
            pressed.set()
        except (EOFError, KeyboardInterrupt):
            pass  # no interactive terminal: wait for the window to be closed instead

    threading.Thread(target=wait_for_enter, daemon=True).start()
    print("\nBrowser left OPEN. Close the browser window, or press Enter here, to finish.")
    while not pressed.is_set() and not page.is_closed():
        try:
            page.wait_for_timeout(500)
        except PlaywrightError:
            break  # window closed


def print_selected(label: str, loc: SelectedLocation) -> None:
    print(f"ACTUAL {label} SELECTED:")
    print(f"  {loc.name}")
    print(f"  {loc.address}")
    print(f"  lat/lng: {loc.latitude}, {loc.longitude}   place id: {loc.place_id} ({loc.provider})")


def browser_config(args: argparse.Namespace) -> BrowserConfig:
    """Browser settings from the CLI. Defaults reproduce the previous behaviour exactly."""
    engine = getattr(args, "browser", DEFAULT_ENGINE)
    if getattr(args, "chromium", False):  # legacy flag, same meaning as --browser chromium
        engine = "chromium"
    
    profile = None
    user_agent = getattr(args, "user_agent", None)
    account_arg = getattr(args, "account", None)
    profile_dir_arg = getattr(args, "profile_dir", None)

    if profile_dir_arg:
        profile = Path(profile_dir_arg)
    elif account_arg:
        acc = get_account(account_arg)
        if not acc:
            available = ", ".join(a.get("account_name", "") for a in load_accounts())
            raise ExtractorError(f"Account {account_arg!r} not found in data/accounts.csv. Available accounts: {available}")
        prof_name = acc.get("profile_dir") or f"browser_profile_{acc['account_name']}"
        profile = BASE_DIR / prof_name
        if not user_agent:
            user_agent = acc.get("user_agent")
    else:
        profile = PROFILE_DIR

    return BrowserConfig(profile_dir=profile, engine=engine, default_profile_dir=PROFILE_DIR, user_agent=user_agent)


def open_worker(args: argparse.Namespace, create_profile: bool = False) -> PlaywrightWorker:
    """
    Browser worker described by the CLI options (visible browser, persistent profile), validated but not
    started: use it as `with open_worker(args) as worker:` (or call .start() / .close() explicitly).
    """
    try:
        cfg = browser_config(args)
        if (create_profile or getattr(args, "account", None)) and not Path(cfg.profile_dir).exists():
            Path(cfg.profile_dir).mkdir(parents=True, exist_ok=True)
        worker = PlaywrightWorker(cfg)
        worker.check_profile()
    except ValueError as exc:
        raise ExtractorError(str(exc))
    return worker


def save_screenshot(page: Page, label: str) -> Path | None:
    """Save a full-page screenshot for debugging. Returns the path, or None if it failed."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{label}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return path
    except PlaywrightError:
        return None


def ensure_logged_in(page: Page) -> None:
    """
    Open the rider booking page and confirm Uber treats this profile as logged in.
    Raises LoginRequiredError / SecurityChallengeError with a clear reason otherwise.
    Never enters credentials or interacts with challenges.
    """
    # No "networkidle" wait: the live map keeps loading tiles, so it cost a fixed ~15s per check.
    page.goto(UBER_BOOKING_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)

    host = page.url.split("/")[2] if "://" in page.url else page.url
    if host.startswith("auth.") or "/login" in page.url:
        raise LoginRequiredError(
            f"Uber redirected to the login page ({host}). Session is missing or expired -- "
            "run `python uber_prices.py open` and log in manually again.")

    # Logged-in booking page shows the "Activity" nav link and the pickup field; logged-out shows "Log in".
    try:
        location_field(page, "pickup").wait_for(state="visible", timeout=20_000)
    except PlaywrightError:
        body = page.inner_text("body").lower()
        if any(marker in body for marker in CHALLENGE_MARKERS):
            shot = save_screenshot(page, "security-challenge")
            raise SecurityChallengeError(
                "Uber is showing a security challenge (CAPTCHA/verification). Complete it manually via "
                f"`python uber_prices.py open`. Screenshot: {shot}")
        shot = save_screenshot(page, "booking-page-not-loaded")
        raise ExtractorError(f"Booking page did not load (url: {page.url}). Screenshot: {shot}")
    try:
        page.get_by_text("Activity", exact=True).first.wait_for(state="visible", timeout=10_000)
        activity_visible = True
    except PlaywrightError:
        activity_visible = False
    login_visible = page.get_by_role("link", name="Log in").count() + page.get_by_role("button", name="Log in").count() > 0
    if login_visible or not activity_visible:
        shot = save_screenshot(page, "login-required")
        raise LoginRequiredError(
            "Uber does not show this profile as logged in. Run `python uber_prices.py open` and log in "
            f"manually again. Screenshot: {shot}")


def cmd_check_session(args: argparse.Namespace) -> int:
    with open_worker(args) as worker:
        page = worker.page
        ensure_logged_in(page)
        print(f"[ok] Session is valid -- Uber shows this profile as logged in ({page.url}).")
        return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    sub = getattr(args, "account_subcommand", "list")
    if sub == "list" or sub is None:
        accounts = load_accounts()
        print(f"\n[ACCOUNTS] {len(accounts)} registered mobile account(s) in {ACCOUNTS_CSV.name}:")
        print(f"{'Account Name':<14} {'Phone Number':<16} {'Status':<10} {'Profile Dir':<26} {'User-Agent Platform/Version':<32} {'Notes'}")
        print("-" * 115)
        for a in accounts:
            ua = a.get("user_agent", "")
            ua_summary = "default"
            if "macintosh" in ua.lower():
                ua_summary = "macOS Chrome"
            elif "linux" in ua.lower():
                ua_summary = "Linux Chrome"
            elif "windows" in ua.lower():
                ua_summary = "Windows Chrome"
            print(f"{a.get('account_name', ''):<14} {a.get('phone_number', ''):<16} {a.get('status', ''):<10} {a.get('profile_dir', ''):<26} {ua_summary:<32} {a.get('notes', '')}")
        print()
        print("Usage tips:")
        print("  Log in an account:  python uber_prices.py open --account Account_1")
        print("  Check session:      python uber_prices.py check-session --account Account_1")
        print("  Run batch:          python uber_prices.py batch --account Account_1 --rotate-accounts")
        return 0
    elif sub == "add":
        name, phone = args.name, args.phone
        prof = args.profile_dir or f"browser_profile_{name.lower().replace(' ', '_')}"
        ua = args.user_agent or random.choice(REALISTIC_USER_AGENTS)
        accounts = load_accounts()
        if any(a.get("account_name", "").lower() == name.lower() for a in accounts):
            print(f"[error] Account '{name}' already exists.")
            return 1
        new_acc = {"account_name": name, "phone_number": phone, "profile_dir": prof, "status": "active", "last_used": "", "user_agent": ua, "notes": args.notes or ""}
        accounts.append(new_acc)
        save_accounts(accounts)
        print(f"[ok] Registered account '{name}' ({phone}) -> {prof}/ [User-Agent: {ua[:40]}...]")
        return 0
    elif sub == "setup":
        acc_id = args.account or "Account_1"
        args.account = acc_id
        return cmd_open(args)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    account_arg = getattr(args, "account", None)
    with open_worker(args, create_profile=True) as worker:
        page = worker.page
        page.set_default_timeout(NAV_TIMEOUT_MS)
        print(f"[DEBUG] Launching browser with headless={worker.config.headless}...")
        print(f"[DEBUG] Engine used: {worker.engine_used}")
        print(f"[DEBUG] Executable path: {worker.browser_executable()}")
        print(f"[DEBUG] Browser Process ID (PID): {worker.browser_pid()}")
        print(f"[DEBUG] Profile directory: {worker.config.profile_dir}")
        print(f"[DEBUG] User-Agent: {worker.config.user_agent or 'Default'}")
        try:
            page.goto(UBER_HOME_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                page.bring_to_front()
                if worker.context and worker.context.pages:
                    worker.context.pages[0].bring_to_front()
            except PlaywrightError:
                pass
            time.sleep(3)
        except PlaywrightError as exc:
            shot = save_screenshot(page, "open-failed")
            print(f"[error] Could not load {UBER_HOME_URL}: {exc.message.splitlines()[0]}")
            if shot:
                print(f"        Screenshot: {shot}")
            return 1

        print(f"[ok] Browser opened: {page.url}")
        print(f"     Page title: {page.title()!r}")
        print(f"     Profile dir: {worker.config.profile_dir}")
        print()
        print("========================================")
        print("BROWSER SHOULD BE VISIBLE NOW ON YOUR SCREEN")
        print("Look for a Chrome window titled Uber")
        print("If you still don't see it, check Taskbar or other monitors")
        print("========================================")
        print()
        try:
            input(">>> Browser should now be visible. Please log in with +916353487984 and press Enter here when done.\n")
        except (EOFError, KeyboardInterrupt):
            pass

    if account_arg:
        update_account_status(account_arg, "active", notes="Logged in via `open` command")
    print(f"[ok] Browser closed. Session saved in {worker.config.profile_dir.name}/.")
    return 0


# Read-only: values are only read, never set.
BROWSER_INFO_JS = """
() => ({userAgent: navigator.userAgent, platform: navigator.platform, language: navigator.language,
        languages: navigator.languages, webdriver: navigator.webdriver,
        innerSize: [window.innerWidth, window.innerHeight]})
"""


def cmd_browser_info(args: argparse.Namespace) -> int:
    """Read-only: report the browser environment the worker actually uses. Performs no Uber action."""
    with open_worker(args) as worker:
        page = worker.page
        # The persistent profile may restore its last tab; use a blank page so no site is loaded or acted on.
        if page.url != "about:blank":
            page.goto("about:blank")
        info = page.evaluate(BROWSER_INFO_JS)
        cfg = worker.config
        version = None
        if worker.engine_used in ("chrome", "chromium"):
            try:  # exact build; the User-Agent string reports a reduced "154.0.0.0"
                version = worker.context.new_cdp_session(page).send("Browser.getVersion")["product"]
            except PlaywrightError:
                pass
        if version is None:
            match = re.search(r"(Firefox|Version|Chrome)/([\d.]+)", info["userAgent"])
            version = f"{match.group(1)} {match.group(2)} (from User-Agent)" if match else None
        # Only a yes/no: no cookie names, values or counts are printed.
        has_session = any("uber.com" in c["domain"] for c in worker.context.cookies())
        viewport = page.viewport_size
        rows = [
            ("Browser type", f"{worker.engine_used}" + (" (installed Google Chrome)" if worker.engine_used == "chrome" else "")),
            ("Executable path", worker.browser_executable()),
            ("Browser version", version),
            ("Playwright version", worker.playwright_version()),
            ("navigator.userAgent", info["userAgent"]),
            ("navigator.platform", info["platform"]),
            ("navigator.language", info["language"]),
            ("navigator.languages", info["languages"]),
            ("navigator.webdriver", info["webdriver"]),
            ("Viewport", f"{viewport['width']}x{viewport['height']}" if viewport
             else f"window size {info['innerSize'][0]}x{info['innerSize'][1]}"),
            ("Persistent profile", f"yes: {cfg.profile_dir}"),
            ("Saved Uber session in profile", "yes" if has_session else "no"),
        ]
        print("[browser-info] read-only diagnostic; no Uber page was opened\n")
        for label, value in rows:
            print(f"  {label + ':':<31}{value}")
    return 0


def cmd_locations(args: argparse.Namespace) -> int:
    """Step 5-6: enter and verify both locations. Does not look at prices or click anything else."""
    with open_worker(args) as worker:
        page = worker.page
        page.set_default_timeout(30_000)
        try:
            ensure_logged_in(page)
            print("[ok] Session is logged in.")
            pickup, destination = select_route(page, args.pickup, args.destination)
            print()
            print_selected("PICKUP", pickup)
            print_selected("DESTINATION", destination)
            print(f"\n[ok] Both locations verified. Page: {urlparse(page.url).path} (nothing else was clicked).")
            save_screenshot(page, "locations-selected")
            return 0
        except PlaywrightError as exc:
            shot = save_screenshot(page, "playwright-error")
            raise ExtractorError(f"Browser error: {exc.message.splitlines()[0]}. Screenshot: {shot}")


def cmd_search(args: argparse.Namespace) -> int:
    """Step 7: select + verify both locations, click Search, wait for priced ride options, leave browser open."""
    with open_worker(args) as worker:
        page = worker.page
        page.set_default_timeout(30_000)
        try:
            ensure_logged_in(page)
            print("[ok] Session is logged in.")
            pickup, destination = select_route(page, args.pickup, args.destination)
            print()
            print_selected("PICKUP", pickup)
            print_selected("DESTINATION", destination)
            priced_rows = search_rides(page, pickup, destination)
            shot = save_screenshot(page, "ride-options")
            print(f"\n[ok] Ride options page loaded: {priced_rows} ride option(s) showing a price.")
            print(f"     Route still verified (same place ids). Screenshot: {shot}")
            print("     Nothing was requested or booked. Price extraction is not implemented yet.")
            return 0
        except PlaywrightError as exc:
            if page.is_closed():
                raise ExtractorError("The browser was closed or crashed during the run.")
            shot = save_screenshot(page, "playwright-error")
            raise ExtractorError(f"Browser error: {exc.message.splitlines()[0]}. Screenshot: {shot}")
        finally:
            if not page.is_closed() and not args.close:
                keep_browser_open(page)


def cmd_prices(args: argparse.Namespace) -> int:
    """Steps 8-10: select + verify route, Search, extract ride prices, validate, save JSON. Read-only."""
    with open_worker(args) as worker:
        page = worker.page
        page.set_default_timeout(30_000)
        try:
            ensure_logged_in(page)
            print("[ok] Session is logged in.")
            pickup, destination = select_route(page, args.pickup, args.destination)
            priced_rows = search_rides(page, pickup, destination)
            fetched_at = datetime.now()
            rides, priced_rows = extract_rides_stable(page)
            validate_rides(rides, priced_rows)
            shot = save_screenshot(page, "prices-extracted")

            print()
            print_selected("PICKUP", pickup)
            print_selected("DESTINATION", destination)
            print("RIDE PRICES:")
            for r in rides:
                was = f"  (was {r.original_price})" if r.original_price else ""
                print(f"  {r.ride_type}: {r.price}{was}")
            path = save_result(pickup, destination, rides, fetched_at)
            print(f"\n[ok] {len(rides)} ride prices extracted and validated. Nothing was requested or booked.")
            print(f"     JSON: {path}")
            print(f"     Screenshot: {shot}")
            return 0
        except PlaywrightError as exc:
            if page.is_closed():
                raise ExtractorError("The browser was closed or crashed during the run.")
            shot = save_screenshot(page, "playwright-error")
            raise ExtractorError(f"Browser error: {exc.message.splitlines()[0]}. Screenshot: {shot}")
        finally:
            if args.keep_open and not page.is_closed():
                keep_browser_open(page)


# --- Batch: one browser, sequential routes -------------------------------------------------------

ROUTES_FILE = BASE_DIR / "routes.json"

# Route statuses (machine-readable; also used by the future Laravel importer).
SUCCESS = "SUCCESS"
AUTH_REQUIRED = "AUTH_REQUIRED"
SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
LOCATION_AMBIGUOUS = "LOCATION_AMBIGUOUS"  # no exact match, or several matches -- nothing was selected
SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"  # Uber returned no suggestion list for the typed text
SEARCH_UNAVAILABLE_STOP_AFTER = 2  # consecutive routes; later routes would most likely fail the same way
WRONG_LOCATION = "WRONG_LOCATION"  # Uber's selected place differs from the verified one, or is outside the region
PRICING_NOT_LOADED = "PRICING_NOT_LOADED"
PRICE_NOT_FOUND = "PRICE_NOT_FOUND"
TIMEOUT = "TIMEOUT"
NETWORK_ERROR = "NETWORK_ERROR"
BROWSER_CRASH = "BROWSER_CRASH"
CONFIG_ERROR = "CONFIG_ERROR"
ERROR = "ERROR"
SKIPPED_MULTI_STOP = "SKIPPED_MULTI_STOP"
NOT_RUN = "NOT_RUN"  # batch stopped before this route
# Failures after which continuing makes no sense (every later route would fail the same way).
STOP_BATCH_STATUSES = {AUTH_REQUIRED, SECURITY_CHALLENGE, BROWSER_CRASH}


def classify_failure(exc: Exception) -> str:
    if isinstance(exc, LoginRequiredError):
        return AUTH_REQUIRED
    if isinstance(exc, SecurityChallengeError):
        return SECURITY_CHALLENGE
    if isinstance(exc, SearchUnavailableError):
        return SEARCH_UNAVAILABLE
    if isinstance(exc, (LocationNotFoundError, AmbiguousLocationError)):
        return LOCATION_AMBIGUOUS
    if isinstance(exc, WrongLocationSelectedError):
        return WRONG_LOCATION
    if isinstance(exc, RideOptionsNotLoadedError):
        return PRICING_NOT_LOADED
    if isinstance(exc, PriceNotFoundError):
        return PRICE_NOT_FOUND
    if isinstance(exc, PlaywrightTimeoutError):
        return TIMEOUT
    if isinstance(exc, PlaywrightError):
        msg = exc.message or ""
        if "net::ERR" in msg:
            return NETWORK_ERROR
        if "closed" in msg.lower() or "crash" in msg.lower():
            return BROWSER_CRASH
    return ERROR


def open_booking_page(page: Page) -> None:
    """Fresh booking page for the next route (same tab). Detects expired session / challenges."""
    page.goto(UBER_BOOKING_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    host = urlparse(page.url).netloc
    if host.startswith("auth.") or "/login" in page.url:
        raise LoginRequiredError(f"Uber redirected to the login page ({host}): session expired.")
    try:
        location_field(page, "pickup").wait_for(state="visible", timeout=20_000)
    except PlaywrightError:
        body = page.inner_text("body").lower()
        if any(marker in body for marker in CHALLENGE_MARKERS):
            raise SecurityChallengeError("Uber is showing a security challenge; complete it manually.")
        if page.get_by_role("button", name="Log in").count() or page.get_by_role("link", name="Log in").count():
            raise LoginRequiredError("Uber shows 'Log in': session expired.")
        raise ExtractorError(f"Booking page did not load (url: {page.url}).")


UBER_ROUTE_URL = "https://m.uber.com/go/drop"


def resolve_place(page: Page, spec: LocationSpec, region: dict | None, role: str,
                  timings: dict | None = None, human_delays: bool = True,
                  delay_scale: float = 1.0) -> tuple[SelectedLocation, str]:
    """
    Select `spec` via the pickup field of a FRESH /go/home page and return the verified place plus Uber's own
    JSON for it. Measured 2026-09-30: only a fresh /go/home offers cities/neighbourhoods ("Miami -- FL, USA");
    once any place is set (/go/drop), both fields switch to a businesses-only list.
    """
    timings = {} if timings is None else timings
    t = time.perf_counter()
    open_booking_page(page)
    timings["page_load"] = round(timings.get("page_load", 0) + time.perf_counter() - t, 2)
    _, _, loc = select_location(page, "pickup", spec, verbose=False, timings=timings,
                                human_delays=human_delays, delay_scale=delay_scale)
    check_region(loc, region, role)
    raw = place_json_from_url(page, "pickup")
    if not raw:
        raise WrongLocationSelectedError(f"Could not read Uber's place data for {role} {spec.name!r}.")
    return loc, raw


def open_route(page: Page, pickup: SelectedLocation, pickup_raw: str,
               destination: SelectedLocation, destination_raw: str) -> None:
    """
    Open Uber's route form with both verified places -- the same /go/drop?pickup=..&drop[0]=.. URL Uber itself
    produces when both are chosen in the form -- then check Uber accepted exactly those places.
    """
    page.goto(f"{UBER_ROUTE_URL}?{urlencode({'pickup': pickup_raw, 'drop[0]': destination_raw})}",
              wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    host = urlparse(page.url).netloc
    if host.startswith("auth.") or "/login" in page.url:
        raise LoginRequiredError(f"Uber redirected to the login page ({host}): session expired.")
    # Uber either shows the form with a Search button, or goes straight on to the ride options page
    # ("Choose a ride", whose prices may take a while longer -- that wait belongs to wait_for_ride_options).
    try:
        page.locator('button[aria-label="Search"]').or_(
            page.get_by_role("heading", name="Choose a ride")).first.wait_for(state="visible", timeout=20_000)
    except PlaywrightError:
        raise ExtractorError(f"Route page did not load (url: {urlparse(page.url).path}).")
    verify_route_in_url(page, pickup, destination)
    # The form must also *display* both chosen places (not just carry them in the URL).
    for role, loc in (("pickup", pickup), ("destination", destination)):
        container = page.locator('[data-testid="pudo-select-v2"]').filter(
            has=page.locator(f'[data-testid="{FIELD_ICON_TESTID[role]}"]'))
        # Compare punctuation-insensitively: the form shows "Victory Restaurant & Lounge" while Uber's stored
        # name is "Victory Restaurant  Lounge". Same 10s allowance as before for the form to render.
        deadline = time.perf_counter() + 10
        shown = ""
        while True:
            shown = container.first.inner_text().strip() if container.count() else ""
            if normalize(loc.name) in normalize(shown):
                break
            if time.perf_counter() > deadline:
                raise WrongLocationSelectedError(
                    f"Route form shows {shown or 'nothing'!r} as {role}, expected {loc.name!r}.")
            page.wait_for_timeout(250)


def check_region(loc: SelectedLocation, region: dict | None, role: str) -> None:
    """Physical-location safety net: the place Uber selected must lie inside the batch region."""
    if not region:
        return
    lat, lng = loc.latitude, loc.longitude
    if lat is None or lng is None or not (region["min_lat"] <= lat <= region["max_lat"]
                                          and region["min_lng"] <= lng <= region["max_lng"]):
        raise WrongLocationSelectedError(
            f"Selected {role} {loc.name!r} ({loc.address}) at {lat},{lng} is outside {region['name']}.")


class BrowserMonitor:
    """Resource usage of the Chrome instance using our profile (browser process + all its children)."""

    def __init__(self, profile_dir: Path = PROFILE_DIR):
        self._root = None
        self._profile_dir = profile_dir
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            self._psutil = None

    def _find_root(self):
        marker = f"--user-data-dir={self._profile_dir}".lower()
        for proc in self._psutil.process_iter(["cmdline"]):
            cmd = " ".join(proc.info["cmdline"] or []).lower()
            if marker in cmd and "--type=" not in cmd:
                return proc
        return None

    def snapshot(self, context: BrowserContext, page: Page) -> dict:
        snap = {"open_pages": len(context.pages), "chrome_processes": None, "chrome_rss_mb": None, "js_heap_mb": None}
        try:
            heap = page.evaluate("() => performance.memory ? performance.memory.usedJSHeapSize : null")
            snap["js_heap_mb"] = round(heap / 1e6, 1) if heap else None
        except PlaywrightError:
            pass
        if self._psutil:
            try:
                if self._root is None or not self._root.is_running():
                    self._root = self._find_root()
                if self._root:
                    procs = [self._root] + self._root.children(recursive=True)
                    rss = 0
                    for proc in procs:
                        try:
                            rss += proc.memory_info().rss
                        except self._psutil.Error:
                            pass
                    snap["chrome_processes"] = len(procs)
                    snap["chrome_rss_mb"] = round(rss / 1e6, 1)
            except self._psutil.Error:
                pass
        return snap


class PhaseTimer:
    def __init__(self):
        self.timings: dict[str, float] = {}

    @contextmanager
    def phase(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = round(time.perf_counter() - start, 2)


def run_route(page: Page, route: dict, locations: dict, region: dict | None,
              human_delays: bool = True, delay_scale: float = 1.0) -> dict:
    """Process one pickup -> destination route in the already-open page. Never raises; returns a record."""
    rec = {
        "id": route["id"], "category": route.get("category"),
        "pickup_label": route["pickup"], "destination_label": route["destination"],
        "status": None, "reason": None, "failed_phase": None,
        "started_at": datetime.now().isoformat(timespec="seconds"), "ended_at": None,
        "timings": {}, "location_breakdown": {}, "prices_count": 0, "result": None, "screenshot": None,
    }
    if route.get("stops"):
        rec.update(status=SKIPPED_MULTI_STOP, reason="Multi-stop routes are not implemented yet.",
                   ended_at=rec["started_at"])
        return rec
    missing = [label for label in (route["pickup"], route["destination"]) if label not in locations]
    if missing:
        rec.update(status=CONFIG_ERROR, reason=f"No location mapping in routes.json for {missing}",
                   ended_at=rec["started_at"])
        return rec

    pickup_spec = LocationSpec.from_config({**locations[route["pickup"]], "label": route["pickup"]})
    dest_spec = LocationSpec.from_config({**locations[route["destination"]], "label": route["destination"]})
    timer = PhaseTimer()
    start = time.perf_counter()
    phase = "location_selection"
    try:
        with timer.phase("location_selection"):
            pickup, pickup_raw = resolve_place(page, pickup_spec, region, "pickup",
                                               rec["location_breakdown"], human_delays=human_delays, delay_scale=delay_scale)
            destination, destination_raw = resolve_place(page, dest_spec, region, "destination",
                                                         rec["location_breakdown"], human_delays=human_delays, delay_scale=delay_scale)
        phase = "route_form"
        with timer.phase("route_form"):
            open_route(page, pickup, pickup_raw, destination, destination_raw)
        phase = "search_navigation"
        with timer.phase("search_navigation"):
            if "/go/product-selection" in page.url:
                priced_rows = wait_for_ride_options(page, pickup, destination)  # Uber skipped the form
            else:
                priced_rows = search_rides(page, pickup, destination, screenshot_before=False,
                                           human_delays=human_delays, delay_scale=delay_scale)
        phase = "price_extraction"
        with timer.phase("price_extraction"):
            fetched_at = datetime.now()
            rides, priced_rows = extract_rides_stable(page)
            validate_rides(rides, priced_rows)
        rec["result"] = route_result(pickup, destination, rides, fetched_at)
        rec["prices_count"] = len(rides)
        rec["status"] = SUCCESS
    except (ExtractorError, PlaywrightError) as exc:
        rec["status"] = classify_failure(exc)
        rec["reason"] = exc.message if isinstance(exc, PlaywrightError) else str(exc)
        rec["failed_phase"] = phase
        if not page.is_closed():
            rec["screenshot"] = str(save_screenshot(page, f"batch-route{route['id']:02d}-{rec['status'].lower()}"))
    rec["timings"] = {**timer.timings, "total": round(time.perf_counter() - start, 2)}
    rec["ended_at"] = datetime.now().isoformat(timespec="seconds")
    return rec


def print_route_record(rec: dict, n: int, total: int) -> None:
    arrow = f"{rec['pickup_label']} → {rec['destination_label']}"
    print(f"\n[ROUTE {n:02d}/{total}] #{rec['id']} {arrow}")
    t = rec["timings"]
    for key, label in (("location_selection", "Location selection"), ("route_form", "Route form"),
                       ("search_navigation", "Search/navigation"), ("price_extraction", "Price extraction"),
                       ("total", "Total")):
        if key in t:
            print(f"  {label + ':':<20}{t[key]:.1f}s")
        if key == "location_selection" and rec.get("location_breakdown"):
            # typing_retries is a COUNT; every other breakdown entry is seconds.
            print("    (" + ", ".join(f"typing retries: {v}" if k == "typing_retries" else f"{k} {v:.1f}s"
                                      for k, v in rec["location_breakdown"].items()) + ")")
    if rec["status"] == SUCCESS:
        res = rec["result"]
        print(f"  Selected: {res['pickup']} ({res['pickup_details']['address']})")
        print(f"        ->  {res['destination']} ({res['destination_details']['address']})")
        print(f"  Prices: {rec['prices_count']}  " +
              ", ".join(f"{r['ride_type']} {r['price']}" for r in res["rides"]))
    print(f"  Status: {rec['status']}")
    if rec["reason"]:
        for line in rec["reason"].splitlines()[:8]:
            print(f"    {line}")
    if rec.get("resources", {}).get("chrome_rss_mb") is not None:
        r = rec["resources"]
        print(f"  Browser: {r['chrome_rss_mb']:.0f} MB RSS, {r['chrome_processes']} procs, "
              f"{r['open_pages']} page(s), JS heap {r['js_heap_mb']} MB")


def summarize_batch(records: list[dict], total_seconds: float, launches: int) -> dict:
    attempted = [r for r in records if r["status"] not in (SKIPPED_MULTI_STOP, NOT_RUN, CONFIG_ERROR)]
    ok = [r for r in attempted if r["status"] == SUCCESS]
    durations = sorted(r["timings"]["total"] for r in attempted if "total" in r["timings"])
    median = durations[len(durations) // 2] if durations else 0
    slow = [
        {"id": r["id"], "route": f"{r['pickup_label']} → {r['destination_label']}", "seconds": r["timings"]["total"]}
        for r in attempted if r["timings"].get("total", 0) > max(1.5 * median, median + 5)
    ]
    phase_avg = {}
    for key in ("location_selection", "route_form", "search_navigation", "price_extraction"):
        vals = [r["timings"][key] for r in ok if key in r["timings"]]
        phase_avg[key] = round(sum(vals) / len(vals), 2) if vals else None
    breakdown_avg = {}
    for r in ok:
        for k, v in r.get("location_breakdown", {}).items():
            breakdown_avg.setdefault(k, []).append(v)
    breakdown_avg = {k: round(sum(v) / len(v), 2) for k, v in breakdown_avg.items() if k != "typing_retries"}
    typing_retries_total = sum(r.get("location_breakdown", {}).get("typing_retries", 0) for r in records)
    rss =[r["resources"]["chrome_rss_mb"] for r in records if r.get("resources", {}).get("chrome_rss_mb")]
    pages = [r["resources"]["open_pages"] for r in records if r.get("resources")]
    total_prices = sum(r["prices_count"] for r in ok)
    return {
        "routes_total": len(records),
        "routes_attempted": len(attempted),
        "successful": len(ok),
        "failed": len(attempted) - len(ok),
        "skipped_multi_stop": sum(r["status"] == SKIPPED_MULTI_STOP for r in records),
        "not_run": sum(r["status"] == NOT_RUN for r in records),
        "config_errors": sum(r["status"] == CONFIG_ERROR for r in records),
        "total_seconds": round(total_seconds, 1),
        "average_route_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "median_route_seconds": median,
        "fastest_route_seconds": durations[0] if durations else None,
        "slowest_route_seconds": durations[-1] if durations else None,
        "average_phase_seconds_successful": phase_avg,
        "average_location_breakdown_seconds_successful": breakdown_avg,  # summed over pickup + destination
        "typing_retries_total": typing_retries_total,  # count
        "browser_launches": launches,
        "total_prices_extracted": total_prices,
        "average_prices_per_successful_route": round(total_prices / len(ok), 1) if ok else 0,
        "unusually_long_routes": slow,
        "failures": [{"id": r["id"], "route": f"{r['pickup_label']} → {r['destination_label']}",
                      "status": r["status"], "phase": r["failed_phase"], "reason": (r["reason"] or "").splitlines()[0]}
                     for r in records if r["status"] not in (SUCCESS, SKIPPED_MULTI_STOP)],
        "memory": {"chrome_rss_mb_first": rss[0] if rss else None, "chrome_rss_mb_last": rss[-1] if rss else None,
                   "chrome_rss_mb_max": max(rss) if rss else None, "max_open_pages": max(pages) if pages else None},
    }


def write_batch_files(batch_id: str, meta: dict, records: list[dict], summary: dict | None) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / f"{batch_id}.json"
    perf_path = RESULTS_DIR / f"{batch_id}-performance.json"
    results = {
        **meta,
        "routes": [
            {"id": r["id"], "category": r["category"], "pickup_label": r["pickup_label"],
             "destination_label": r["destination_label"], "status": r["status"], "reason": r["reason"],
             **(r["result"] or {"pickup": None, "destination": None, "fetched_at": None, "currency": None, "rides": []})}
            for r in records
        ],
    }
    perf = {
        **meta,
        "summary": summary,
        "routes": [{k: r.get(k) for k in ("id", "category", "pickup_label", "destination_label", "status",
                                          "failed_phase", "started_at", "ended_at", "timings",
                                          "location_breakdown", "prices_count",
                                          "resources", "screenshot")} for r in records],
    }
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    perf_path.write_text(json.dumps(perf, ensure_ascii=False, indent=2), encoding="utf-8")
    return results_path, perf_path


# --- CSV input / output ---------------------------------------------------------------------------

ROUTES_CSV = BASE_DIR / "data" / "routes.csv"
RESULTS_CSV = BASE_DIR / "data" / "results.csv"
RESULTS_CSV_FIELDS = [
    "run_id", "timestamp", "route_id", "source", "destination", "status", "reason",
    "ride_name", "current_price", "original_price", "currency", "price_value", "original_price_value",
    "pickup_place_id", "destination_place_id",
]


def load_routes_csv(path: Path) -> list[dict]:
    """
    Read route_id,source,destination[,category,stops,...] rows into the route dicts run_route() already uses.
    source/destination are labels that must exist in routes.json "locations" (exact-place mapping).
    Other columns (distance, duration, notes) are reference data and are not used by the scraper.
    """
    if not path.exists():
        raise ExtractorError(f"Route input file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = {"route_id", "source", "destination"} - set(reader.fieldnames or [])
        if missing:
            raise ExtractorError(f"{path} is missing column(s): {sorted(missing)}")
        routes, seen = [], set()
        for line_no, row in enumerate(reader, start=2):
            route_id = (row.get("route_id") or "").strip()
            source = (row.get("source") or "").strip()
            destination = (row.get("destination") or "").strip()
            if not (route_id or source or destination):
                continue  # blank line
            if not route_id.isdigit() or not source or not destination:
                raise ExtractorError(f"{path} line {line_no}: need a numeric route_id, a source and a destination.")
            if int(route_id) in seen:
                raise ExtractorError(f"{path} line {line_no}: duplicate route_id {route_id}.")
            seen.add(int(route_id))
            route = {"id": int(route_id), "pickup": source, "destination": destination,
                     "category": (row.get("category") or "").strip() or None}
            # Intermediate stops ("A|B"). A route with stops is never priced as a direct trip:
            # run_route() marks it SKIPPED_MULTI_STOP until multi-stop support exists.
            stops = [s.strip() for s in (row.get("stops") or "").split("|") if s.strip()]
            if stops:
                route["stops"] = stops
            routes.append(route)
    return routes


def result_csv_rows(batch_id: str, rec: dict) -> list[dict]:
    """One row per extracted ride for a successful route; one status row for any other outcome."""
    base = {"run_id": batch_id, "route_id": rec["id"], "source": rec["pickup_label"],
            "destination": rec["destination_label"], "status": rec["status"],
            "reason": (rec.get("reason") or "").splitlines()[0] if rec.get("reason") else ""}
    res = rec.get("result")
    if rec["status"] == SUCCESS and res:
        return [{**base, "timestamp": res["fetched_at"], "ride_name": ride["ride_type"],
                 "current_price": ride["price"], "original_price": ride["original_price"] or "",
                 "currency": res["currency"], "price_value": ride["price_value"],
                 "original_price_value": ride["original_price_value"] if ride["original_price_value"] is not None else "",
                 "pickup_place_id": res["pickup_details"]["place_id"],
                 "destination_place_id": res["destination_details"]["place_id"]}
                for ride in res["rides"]]
    when = rec.get("ended_at") or datetime.now().isoformat(timespec="seconds")
    return [{**base, "timestamp": when}]


def append_results_csv(path: Path, batch_id: str, recs: list[dict]) -> int:
    """
    Append rows for finished routes (history is never overwritten). Each call writes complete rows in one
    write and fsyncs, so an interrupted run leaves every earlier route's rows intact. Handles file lock retries
    if Excel has the CSV open.
    """
    rows = [row for rec in recs for row in result_csv_rows(batch_id, rec)]
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, 4):
        try:
            is_new = not path.exists() or path.stat().st_size == 0
            if not is_new:
                with path.open(newline="", encoding="utf-8-sig") as f:
                    header = next(csv.reader(f), [])
                if header != RESULTS_CSV_FIELDS:
                    raise ExtractorError(f"{path} has different columns {header}; not appending to avoid mixing formats.")
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=RESULTS_CSV_FIELDS, lineterminator="\n")
            if is_new:
                writer.writeheader()
            writer.writerows(rows)
            # utf-8-sig: a BOM only when the file is created, so Excel shows ₹/$ correctly; appends add no BOM.
            with path.open("a", newline="", encoding="utf-8-sig") as f:
                f.write(buffer.getvalue())
                f.flush()
                os.fsync(f.fileno())
            return len(rows)
        except PermissionError:
            if attempt < 3:
                print(f"[warn] {path.name} is currently open in Excel or another editor. Retrying append in 2s... (Please close Excel)")
                time.sleep(2)
            else:
                print(f"[warn] Could not write to {path.name} because it is locked by Excel. (JSON results are still saved in data/results/!)")
                return 0
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    config = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    input_path, output_path = Path(args.input), Path(args.output)
    routes = load_routes_csv(input_path)  # the CSV decides WHICH routes run; routes.json maps the places
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        routes = [r for r in routes if r["id"] in wanted]
    locations, region = config["locations"], config.get("region")

    human_delays = not getattr(args, "no_human_delays", False)
    delay_scale = getattr(args, "pause_scale", 1.0)

    started = datetime.now()
    batch_id = f"batch-{started:%Y%m%d-%H%M%S}"
    batch_start = time.perf_counter()
    meta = {"batch_id": batch_id, "started_at": started.isoformat(timespec="seconds"), "finished_at": None,
            "routes_file": str(args.routes), "region": region}
    records: list[dict] = []
    csv_written = 0  # records already appended to the results CSV (each route is written exactly once)

    def flush_csv() -> None:
        nonlocal csv_written
        append_results_csv(output_path, batch_id, records[csv_written:])
        csv_written = len(records)

    print(f"[INPUT]  {input_path} ({len(routes)} route(s) selected)")
    print(f"[OUTPUT] {output_path} (append)")
    runnable = sum(1 for r in routes if not r.get("stops"))
    print(f"[BATCH] {batch_id}: {len(routes)} routes ({runnable} pickup→destination, "
          f"{len(routes) - runnable} multi-stop will be skipped)")

    exit_code = 0
    t = time.perf_counter()
    worker = open_worker(args)
    worker.start()
    monitor = BrowserMonitor(worker.config.profile_dir)
    try:
        context, page = worker.context, worker.page
        page.set_default_timeout(30_000)
        meta["browser_launch_seconds"] = round(time.perf_counter() - t, 2)
        print(f"[BROWSER] Launch: {meta['browser_launch_seconds']:.1f}s")

        t = time.perf_counter()
        try:
            ensure_logged_in(page)
            meta["session_check_seconds"] = round(time.perf_counter() - t, 2)
            print(f"[SESSION] Logged in: {meta['session_check_seconds']:.1f}s")
            meta["resources_after_login"] = monitor.snapshot(context, page)
        except ExtractorError as exc:
            status = classify_failure(exc)
            hint = ("Log in manually via `python uber_prices.py open --account <name>`, then re-run."
                    if status in (AUTH_REQUIRED, SECURITY_CHALLENGE) else "Check the screenshot, then re-run.")
            print(f"[SESSION] {status}: {exc}\n[BATCH] Stopped before any route. {hint}")
            records = [{"id": r["id"], "category": r.get("category"), "pickup_label": r["pickup"],
                        "destination_label": r["destination"], "status": NOT_RUN, "reason": status,
                        "failed_phase": None, "timings": {}, "prices_count": 0, "result": None} for r in routes]
            meta["stopped_reason"] = status
            routes = []
            exit_code = 1

        attach_search_monitor(page)
        search_unavailable_streak = 0
        for n, route in enumerate(routes, 1):
            if n > 1 and human_delays:
                human_pause(2.0, 4.5, scale=delay_scale, enabled=True)
            rec = run_route(page, route, locations, region, human_delays=human_delays, delay_scale=delay_scale)
            if rec["status"] not in (SKIPPED_MULTI_STOP, CONFIG_ERROR):
                rec["resources"] = monitor.snapshot(context, page) if not page.is_closed() else {}
            records.append(rec)
            print_route_record(rec, n, len(routes))
            write_batch_files(batch_id, meta, records, None)
            flush_csv()
            if rec["status"] == SEARCH_UNAVAILABLE:
                search_unavailable_streak += 1
            elif rec["status"] not in (SKIPPED_MULTI_STOP, CONFIG_ERROR):
                search_unavailable_streak = 0
            stop_reason = rec["status"] if rec["status"] in STOP_BATCH_STATUSES else None
            if search_unavailable_streak >= SEARCH_UNAVAILABLE_STOP_AFTER:
                stop_reason = f"{SEARCH_UNAVAILABLE} x{search_unavailable_streak}"
            if stop_reason:
                print(f"\n[BATCH] Stopping: {stop_reason}. Remaining routes are marked NOT_RUN.")
                meta["stopped_reason"] = stop_reason
                for rest in routes[n:]:
                    records.append({"id": rest["id"], "category": rest.get("category"),
                                    "pickup_label": rest["pickup"], "destination_label": rest["destination"],
                                    "status": NOT_RUN, "reason": f"Batch stopped after {stop_reason}",
                                    "failed_phase": None, "timings": {}, "prices_count": 0, "result": None})
                exit_code = 1
                break
        flush_csv()  # NOT_RUN rows from an early stop or a failed session check
    finally:
        t = time.perf_counter()
        worker.close()
        meta["browser_close_seconds"] = round(time.perf_counter() - t, 2)

    total = time.perf_counter() - batch_start
    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary = summarize_batch(records, total, launches=1)
    results_path, perf_path = write_batch_files(batch_id, meta, records, summary)

    s = summary
    print("\n[BATCH SUMMARY]")
    print(f"Routes in file:        {s['routes_total']}  (skipped multi-stop: {s['skipped_multi_stop']}, "
          f"not run: {s['not_run']}, config errors: {s['config_errors']})")
    print(f"Routes attempted:      {s['routes_attempted']}")
    print(f"Successful:            {s['successful']}")
    print(f"Failed:                {s['failed']}")
    print(f"Total time:            {int(total // 60)} min {total % 60:.0f} sec")
    print(f"Browser launch:        {meta['browser_launch_seconds']:.1f}s   Session check: "
          f"{meta.get('session_check_seconds', 0):.1f}s   Close: {meta['browser_close_seconds']:.1f}s")
    if s["average_route_seconds"] is not None:
        print(f"Average route time:    {s['average_route_seconds']} sec (median {s['median_route_seconds']})")
        print(f"Fastest route:         {s['fastest_route_seconds']} sec")
        print(f"Slowest route:         {s['slowest_route_seconds']} sec")
        print(f"Avg phases (success):  " + ", ".join(f"{k} {v}s" for k, v in s["average_phase_seconds_successful"].items()))
        print(f"Avg location detail:   " + ", ".join(
            f"{k} {v}s" for k, v in s["average_location_breakdown_seconds_successful"].items()
            if k != "typing_retries"))
        print(f"Typing retries:        {s['typing_retries_total']} (count, over all routes)")
    print(f"Browser launches:      {s['browser_launches']}")
    print(f"Total prices:          {s['total_prices_extracted']}  "
          f"(avg {s['average_prices_per_successful_route']} per successful route)")
    m = s["memory"]
    print(f"Chrome memory (RSS):   first {m['chrome_rss_mb_first']} MB, last {m['chrome_rss_mb_last']} MB, "
          f"max {m['chrome_rss_mb_max']} MB; max open pages {m['max_open_pages']}")
    if s["unusually_long_routes"]:
        print("Unusually long routes: " + "; ".join(f"#{r['id']} {r['route']} ({r['seconds']}s)"
                                                    for r in s["unusually_long_routes"]))
    if s["failures"]:
        print("Failures:")
        for f in s["failures"]:
            print(f"  #{f['id']} {f['route']}: {f['status']} [{f['phase']}] {f['reason']}")
    print(f"\nResults:     {results_path}\nPerformance: {perf_path}\nCSV:         {output_path} (appended)")
    return exit_code if exit_code else (0 if s["failed"] == 0 else 2)


def add_browser_options(p: argparse.ArgumentParser, suppress: bool) -> None:
    """
    Browser options, accepted before OR after the subcommand. On subcommands the defaults are SUPPRESSed so
    an option given before the subcommand is not overwritten by the subcommand's default.
    """
    d = (lambda value: argparse.SUPPRESS) if suppress else (lambda value: value)
    p.add_argument("--browser", choices=sorted(ENGINES), default=d(DEFAULT_ENGINE),
                   help="Browser engine: chrome = installed Google Chrome (default, falls back to bundled "
                        "Chromium), chromium = Playwright's bundled Chromium, firefox, webkit.")
    p.add_argument("--profile-dir", default=d(None),
                   help="Persistent profile folder (default: browser_profile/, the Chrome profile with the "
                        "Uber login). Firefox/WebKit need their own existing folder.")
    p.add_argument("--account", default=d(None),
                   help="Account name or phone number from data/accounts.csv to use for persistent profile session.")
    p.add_argument("--user-agent", default=d(None),
                   help="Custom User-Agent string to use for browser requests.")
    p.add_argument("--no-human-delays", action="store_true", default=d(False),
                   help="Disable human-like typing and activity pauses.")
    p.add_argument("--pause-scale", type=float, default=d(1.0),
                   help="Multiplier for human pauses (default 1.0; 0.5 = faster, 2.0 = slower).")
    p.add_argument("--chromium", action="store_true", default=d(False),
                   help="Legacy alias for --browser chromium.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Uber price extractor (never books rides).")
    add_browser_options(parser, suppress=False)
    browser_opts = argparse.ArgumentParser(add_help=False)
    add_browser_options(browser_opts, suppress=True)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, parents=[browser_opts])

    command("open", "Open Uber in a visible browser (use this to log in manually).")
    command("check-session", "Verify the saved browser profile is still logged in.")
    command("browser-info", "Read-only: show the browser environment the worker uses (no Uber action).")

    acc_parser = command("accounts", "Manage registered mobile number accounts (list, add, setup).")
    acc_sub = acc_parser.add_subparsers(dest="account_subcommand")
    acc_sub.add_parser("list", help="List registered mobile accounts and their profile status.")
    
    add_acc = acc_sub.add_parser("add", help="Register a new mobile account.")
    add_acc.add_argument("--name", required=True, help="Short identifier (e.g. Account_1).")
    add_acc.add_argument("--phone", required=True, help="Mobile phone number.")
    add_acc.add_argument("--profile-dir", help="Custom browser profile folder name.")
    add_acc.add_argument("--user-agent", help="Custom User-Agent string (random realistic Chrome UA assigned if omitted).")
    add_acc.add_argument("--notes", help="Optional notes for this account.")

    setup_acc = acc_sub.add_parser("setup", help="Open browser to log in a specific account.")
    setup_acc.add_argument("--account", help="Account name or phone number to set up.")

    locations = command("locations", "Enter and verify pickup/destination (no prices).")
    locations.add_argument("--pickup", required=True)
    locations.add_argument("--destination", required=True)

    search = command("search", "Select route, click Search, show ride options (browser stays open).")
    search.add_argument("--pickup", required=True)
    search.add_argument("--destination", required=True)
    search.add_argument("--close", action="store_true", help="Close the browser at the end instead of leaving it open.")

    batch = command("batch", "Run the routes from the input CSV sequentially in ONE browser.")
    batch.add_argument("--input", default=str(ROUTES_CSV),
                       help="Routes to run: CSV with route_id,source,destination[,category] (default: data/routes.csv).")
    batch.add_argument("--output", default=str(RESULTS_CSV),
                       help="CSV that results are APPENDED to, one row per ride (default: data/results.csv).")
    batch.add_argument("--routes", default=str(ROUTES_FILE),
                       help="Place mapping for the source/destination labels + region check (default: routes.json).")
    batch.add_argument("--only", help="Comma-separated route ids from the input CSV to run, e.g. 1,2 (default: all).")
    batch.add_argument("--rotate-accounts", action="store_true",
                       help="Automatically rotate active mobile accounts from data/accounts.csv.")

    prices = command("prices", "Extract ride prices for one route.")
    prices.add_argument("--pickup", required=True)
    prices.add_argument("--destination", required=True)
    prices.add_argument("--keep-open", action="store_true", help="Leave the browser open at the end for checking.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"open": cmd_open, "check-session": cmd_check_session, "browser-info": cmd_browser_info,
                "accounts": cmd_accounts, "locations": cmd_locations,
                "search": cmd_search, "prices": cmd_prices, "batch": cmd_batch}
    try:
        return handlers[args.command](args)
    except (ExtractorError, BrowserLaunchError) as exc:
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
