"""
Uber Session Manager
--------------------
Allows exporting, listing, and opening saved Uber session states (JSON files).
Ishita and Lina can use this to share logged-in account sessions (e.g., Account 4)
so that either user can directly run extraction commands without needing manual re-login.
 
Usage:
  1. Export a logged-in profile session:
     python uber_session_manager.py export --name account4 --profile ./browser_profile_account_4
 
  2. Open and test a saved session:
     python uber_session_manager.py open --name account4
 
  3. List all saved sessions:
     python uber_session_manager.py list
"""
 
import argparse
from pathlib import Path
import time
from playwright.sync_api import sync_playwright
 
from browser_worker import parse_proxy
 
SESSIONS_DIR = Path("saved_sessions")
SESSIONS_DIR.mkdir(exist_ok=True)
 
DEFAULT_PROFILE = Path("./browser_profile_account_4")
 
 
def export_session(profile_path: Path, session_name: str, proxy: str | None = None):
    """Export logged-in session state from a persistent browser profile folder into a JSON state file."""
    output_file = SESSIONS_DIR / f"{session_name}.json"
    if not profile_path.exists():
        print(f"❌ Profile directory does not exist: {profile_path}")
        return
 
    print(f"Exporting session from profile: {profile_path.resolve()}")
    parsed_proxy = parse_proxy(proxy) if proxy else None
    if parsed_proxy:
        print(f"Using proxy: {parsed_proxy['server']}")
    else:
        print("Using direct connection (no proxy)")
    print(f"Saving session file to: {output_file.resolve()}")
 
    with sync_playwright() as p:
        kwargs = {
            "user_data_dir": str(profile_path),
            "headless": False,
            "channel": "chrome",
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if parsed_proxy:
            kwargs["proxy"] = parsed_proxy
 
        context = p.chromium.launch_persistent_context(**kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://m.uber.com/go/home", wait_until="domcontentloaded")
        time.sleep(3)
 
        if "auth.uber.com" in page.url or "login" in page.url:
            print("\n⚠️ The profile is not currently logged into Uber.")
            print("Please log into Uber in the browser window that just opened.")
            input("Press Enter after completing login to save the session...")
 
        context.storage_state(path=str(output_file))
        context.close()
 
    print(f"\n✅ Session saved successfully → {output_file}")
    print(f"You can now share '{output_file.name}' with Lina!")
 
 
def open_session(session_name: str, proxy: str | None = None):
    """Open Uber using a saved session JSON file to verify login state."""
    session_file = SESSIONS_DIR / f"{session_name}.json"
    if not session_file.exists():
        print(f"❌ Session file not found: {session_file}")
        list_sessions()
        return
 
    print(f"Loading session: {session_file.resolve()}")
    parsed_proxy = parse_proxy(proxy) if proxy else None
    if parsed_proxy:
        print(f"Using proxy: {parsed_proxy['server']}")
    else:
        print("Using direct connection (no proxy)")
 
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs = {
            "storage_state": str(session_file),
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
        }
        if parsed_proxy:
            context_kwargs["proxy"] = parsed_proxy
 
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto("https://m.uber.com/go/home", wait_until="domcontentloaded")
        time.sleep(4)
 
        print("\nCurrent URL :", page.url)
        print("Page Title  :", page.title())
 
        if "auth.uber.com" in page.url:
            print("\n❌ Session expired or invalid. Please re-export from the main logged-in profile.")
        else:
            print(f"\n✅ Logged in successfully using session state '{session_name}'!")
 
        input("\nPress Enter to close browser...")
        context.close()
        browser.close()
 
 
def list_sessions():
    """List all saved sessions in saved_sessions/ directory."""
    files = list(SESSIONS_DIR.glob("*.json"))
    if not files:
        print("No saved sessions found in saved_sessions/")
        return
 
    print("\nAvailable saved sessions:")
    for f in files:
        size_kb = f.stat().st_size / 1024
        print(f"  • {f.stem:<15} ({f.name}, {size_kb:.1f} KB)")
 
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Uber Shared Session Manager")
    parser.add_argument(
        "action", choices=["export", "open", "list"], help="Action to perform: export | open | list"
    )
    parser.add_argument(
        "--name", type=str, help="Session name (e.g. account4, lina_shared)"
    )
    parser.add_argument(
        "--profile",
        type=str,
        help="Profile folder path for export action (default: ./browser_profile_account_4)",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        help="Proxy URL (e.g. http://user:pass@disp.oxylabs.io:8004)",
    )
    args = parser.parse_args()
 
    if args.action == "list":
        list_sessions()
    elif args.action == "export":
        if not args.name:
            print("❌ Please provide session name using --name (e.g. --name account4)")
            exit(1)
        profile_path = Path(args.profile) if args.profile else DEFAULT_PROFILE
        export_session(profile_path, args.name, proxy=args.proxy)
    elif args.action == "open":
        if not args.name:
            print("❌ Please provide session name using --name (e.g. --name account4)")
            exit(1)
        open_session(args.name, proxy=args.proxy)
 
 
 
