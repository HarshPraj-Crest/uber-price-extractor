# save_as_account.py
from playwright.sync_api import sync_playwright
from pathlib import Path
import csv
import time
import shutil
import argparse

def save_session_as_account(session_name: str, account_name: str):
    session_file = Path("saved_sessions") / f"{session_name}.json"
    profile_dir = Path(f"browser_profile_{account_name.lower()}")
    csv_file = Path("data/accounts.csv")

    if not session_file.exists():
        print(f"❌ Session file not found: {session_file}")
        return

    # 1. Create clean profile folder
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
    profile_dir.mkdir()

    print(f"Creating profile → {profile_dir}")

    import json, re
    raw_content = session_file.read_text(encoding="utf-8", errors="ignore")
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw_content)
    try:
        state_data = json.loads(cleaned)
    except json.JSONDecodeError:
        state_data = json.loads(cleaned, strict=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=state_data)
        page = context.new_page()
        page.goto("https://m.uber.com/go/home", wait_until="domcontentloaded")
        time.sleep(3)

        cookies = context.cookies()

        persistent = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=True,
            channel="chrome"
        )
        persistent.add_cookies(cookies)

        page2 = persistent.pages[0] if persistent.pages else persistent.new_page()
        page2.goto("https://m.uber.com/go/home", wait_until="domcontentloaded")
        time.sleep(3)

        persistent.close()
        browser.close()

    print(f"✅ Profile created: {profile_dir}")

    # 2. Automatically add to accounts.csv
    if not csv_file.exists():
        print("❌ data/accounts.csv not found")
        return

    # Read existing accounts
    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = list(csv.reader(f))
        headers = reader[0]
        rows = reader[1:]

    # Check if account already exists
    existing_names = [row[0] for row in rows]
    if account_name in existing_names:
        print(f"⚠️  {account_name} already exists in CSV. Skipping CSV update.")
    else:
        # Add new row with default Windows User-Agent
        win_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
        new_row = [account_name, "", str(profile_dir), "active", "", win_ua, "", f"From {session_name}.json"]
        # Adjust columns if needed
        while len(new_row) < len(headers):
            new_row.append("")

        rows.append(new_row)

        with open(csv_file, "w", newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

        print(f"✅ Added {account_name} to data/accounts.csv")

    print("\nDone! Now you can use:")
    print(f"python uber_prices.py open --account {account_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, help="Name of json file (without .json)")
    parser.add_argument("--account", required=True, help="Account name (example: Account_3)")
    args = parser.parse_args()

    save_session_as_account(args.session, args.account)