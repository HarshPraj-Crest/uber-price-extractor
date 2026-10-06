# Uber Price Extractor

A read-only Python and Playwright solution for extracting ride prices from Uber's web interface for fixed point-to-point routes and multi-stop routes in Miami/South Florida.

---

## 1. Project Overview

### What It Does
This project automates the sequential extraction of ride prices (e.g., UberX, UberXL, Comfort, Black SUV) across a list of predefined pickup, multi-stop, and destination routes in Miami using Python, Playwright, multi-account rotation, per-account proxy support, and persistent browser sessions.

### Hard Rules & Constraints
* **Never requests or books rides:** The tool stops once ride options and prices are rendered. No ride requests, payment submissions, or booking buttons are ever clicked.
* **No automated login or MFA/CAPTCHA solving:** Login must be performed manually by a human user in a visible browser per account profile. Session cookies are persisted locally to remain logged in.
* **No bot evasion or spoofing:** Operates transparently using real Google Chrome / Chromium browser instances with human-like typing and mouse pauses.

---

## 2. System Architecture & Workflow

```
+---------------------------+
| data/accounts_example.csv |  (Reference template for data/accounts.csv)
+-------------+-------------+
              |
              v
+---------------------------+
|    data/accounts.csv      |  (Accounts, proxies, user-agents, profile folders)
+-------------+-------------+
              |
              v
+---------------------------+
|     data/routes.csv       |  (Routes to run: pickup, stops, destination)
+-------------+-------------+
              |
              v
+---------------------------+
|   batch --rotate command  |  (Rotates active account automatically)
+-------------+-------------+
              |
              v
+---------------------------+
|     PlaywrightWorker      |  (Launches account profile + per-account proxy)
+-------------+-------------+
              |
              v
+-----------------------------------------------------------------------------------+
| For each route (single or multi-stop):                                            |
|  1. Select & verify pickup place (exact match / alias / address hints)             |
|  2. Select & verify intermediate stops (if any)                                   |
|  3. Select & verify destination place                                             |
|  4. Open Uber route URL or interact with Add Stop UI                              |
|  5. Extract ride types, prices, and original price discounts                      |
|  6. Log real-time runtime duration, Chrome RAM, and System RAM usage             |
+-------------+---------------------------------------------------------------------+
              |
              v
+-----------------------------------------------------------------------------------+
| Output & Tracking Persistence:                                                    |
|  - Append result rows to day/slot CSVs (e.g., data/results/YYYY-MM-DD/slot_<N>.csv)|
|  - Real-time status update in data/rotation_state.json                            |
|  - Write JSON data & performance files in data/results/                           |
+-----------------------------------------------------------------------------------+
```

---

## 3. Folder Structure

```
uber-price-extractor/
├── uber_prices.py                 # Main CLI entry point (subcommands, batch execution, rotation, status)
├── browser_worker.py              # Playwright browser lifecycle wrapper (persistent profiles & proxy)
├── routes.json                    # Location lookup mapping (names, aliases, address hints) & region bounds
├── requirements.txt               # Python package dependencies
├── README.md                      # Comprehensive documentation
├── data/
│   ├── routes.csv                 # Route input definition file (supports multi-stop via `stops` column)
│   ├── accounts.csv               # Active mobile accounts, user-agents, proxies, and profile folders
│   ├── accounts_example.csv       # [Reference Template] Example accounts CSV configuration file
│   ├── rotation_state.json        # [Generated] Live rotation & batch progress state tracking file
│   ├── results/                   # [Generated / Git-ignored] Day-wise subdirectories (e.g. YYYY-MM-DD/slot_7.csv) & JSON files
│   └── screenshots/               # [Generated / Git-ignored] Failure & diagnostic screenshots
└── browser_profile_account*/      # [Generated / Confidential / Git-ignored] Persistent Chrome account session profiles
```

---

## 4. Complete Setup Guide (From Cloning to Running)

Follow these steps to set up the project on a new machine:

### Step 1: Clone the Repository
```cmd
git clone https://github.com/lina-d-crest/uber-price-extractor.git
cd uber-price-extractor
```

### Step 2: Create Python Virtual Environment
```cmd
python -m venv .venv
```

### Step 3: Activate Environment & Install Requirements
```cmd
# On PowerShell:
.\.venv\Scripts\activate

# Upgrade pip and install dependencies:
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Step 4: Install Playwright Chromium Browser
```cmd
python -m playwright install chromium
```

---

## 5. Account Setup & First-Time Login (Saving Session Cookies)

Before running automated price extraction, you must establish and save authenticated session cookies for each mobile account you intend to use.

### Step 1: Register Accounts in `data/accounts.csv`
You can reference `data/accounts_example.csv` or register accounts via the CLI:

```cmd
# List registered accounts
python uber_prices.py accounts list

# Add Account_1
python uber_prices.py accounts add --name Account_1 --phone +15551234567

# Add Account_2
python uber_prices.py accounts add --name Account_2 --phone +15559876543
```

### Step 2: First-Time Login & Save Cookies for `Account_1`
Run the setup command for `Account_1`:

```cmd
python uber_prices.py accounts setup --account Account_1
```

1. A visible Chrome browser opens loading `browser_profile_account1`.
2. Enter your phone number on Uber's login screen and complete OTP / MFA verification manually.
3. Once logged in and viewing the Uber home page (`https://m.uber.com/go/home`), return to your terminal and press `Enter`.
4. Session cookies and authentication tokens are saved in `browser_profile_account1/`.

### Step 3: First-Time Login & Save Cookies for `Account_2` (and additional accounts)
Repeat the setup step for each additional account:

```cmd
python uber_prices.py accounts setup --account Account_2
```

Complete manual login in the visible browser and press `Enter`. Its session cookies will be stored separately in `browser_profile_account2/`.

### Step 4: Verify Account Session Cookies
Cross-check all registered accounts to confirm session cookie databases exist:

```cmd
python uber_prices.py accounts check
```

---

## 6. Account Rotation & Checking Last Used Account

### Run Full Batch with Account Rotation (Recommended)
Automatically selects the next active account in `data/accounts.csv`, loads its persistent browser profile & proxy, and runs all batch routes:

```cmd
python uber_prices.py batch --rotate
```

### Check Last Used Account & System Status
To view **which account was used last**, when it was rotated, completed/remaining routes, and system RAM:

```cmd
python uber_prices.py status
```

**Output Example**:
```text
==================================================
           UBER PRICE EXTRACTOR STATUS            
==================================================
Last Used Account:   Account_2
Last Rotated At:     2026-10-05T16:20:59
Total Batch Runs:    6
Last Batch ID:       batch-20261005-161918
--------------------------------------------------
Completed Routes:    35 / 35
Failed Routes:       0
Remaining Routes:    0
--------------------------------------------------
Current System RAM:  9.46 GB / 11.89 GB (79.5% in use)
--------------------------------------------------
Registered Accounts:
  • Account_1: status=active | proxy=Direct (No Proxy) | last_used=2026-10-05T14:48:11
  • Account_2: status=active | proxy=Direct (No Proxy) | last_used=2026-10-05T16:19:21
==================================================
```

### Run Batch for a Specific Account
```cmd
python uber_prices.py batch --account Account_1
```

### Run Specific Route IDs Only
```cmd
python uber_prices.py batch --rotate --only 1,2,13
```

---

## 7. Managing Proxies Per Account

Assign HTTP, SOCKS5, or authenticated proxies per account in `data/accounts.csv`:

```cmd
# Set HTTP or SOCKS5 proxy for Account_1
python uber_prices.py accounts set-proxy --account Account_1 --proxy http://user:pass@1.2.3.4:8080

# Clear proxy (use direct connection)
python uber_prices.py accounts set-proxy --account Account_1 --proxy direct
```

---

## 8. Route Execution & Multi-Stop Commands

### Single Route Price Extraction
```cmd
python uber_prices.py prices --pickup "Brickell City Centre" --destination "Kaseya Center"
```

### Multi-Stop Route Price Extraction
Supports 1 or more intermediate stops (`Pickup → Stop 1 → Stop 2 → Dropoff`):
```cmd
# Direct URL method (default):
python uber_prices.py prices --pickup "Fontainebleau" --stops "Prime 112" "E11EVEN" --destination "Brickell"

# Interactive UI method (clicks Uber's "Add stop" button in browser):
python uber_prices.py prices --pickup "Fontainebleau" --stops "Prime 112" "E11EVEN" --destination "Brickell" --method ui
```

### Test Location Autocomplete Match
```cmd
python uber_prices.py locations --pickup "Fontainebleau" --stops "Prime 112" "E11EVEN" --destination "Brickell"
```

---

## 9. Tracking Execution Time & Memory Usage

You can monitor runtime duration and memory usage in **4 places**:

1. **Real-Time Progress Line during Batch Execution**:
   Each completed route prints a live progress update showing elapsed batch time, Chrome RAM, and System RAM percentage:
   ```text
   [PROGRESS] Account: Account_1 | Time: 1m 24s | 654 MB Chrome RAM (79.5% System RAM) | Done: 12/35 (Success: 12, Failed: 0, Remaining: 23)
   ```

2. **Per-Route Resource Printout**:
   After each route in console:
   ```text
   [ROUTE 01/35] #1 Brickell City Centre → Kaseya Center
     Location selection: 12.1s
     Route form:         18.3s
     Search/navigation:  5.4s
     Price extraction:   4.2s
     Total:              40.0s
     Status: SUCCESS
     Resource Usage: Chrome 654 MB RSS (9 procs, 1 tab(s), JS heap 137.3 MB) | System RAM: 9.46 GB / 11.89 GB (79.5%)
   ```

3. **Status Command Summary**:
   Run `python uber_prices.py status` to view current system RAM usage, last rotated account, and batch completion stats.

4. **Performance JSON Output**:
   Every batch run saves a complete benchmark file to `data/results/batch-<TIMESTAMP>-performance.json` containing total batch runtime duration, Chrome RSS memory metrics, and per-phase breakdown.

---

## 10. Reference Account Configuration File

A reference template is available at `data/accounts_example.csv`:

```csv
account_name,phone_number,profile_dir,status,last_used,user_agent,proxy,notes
Account_1,+15551234567,browser_profile_account1,active,,"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",,Primary account
Account_2,+15559876543,browser_profile_account2,active,,"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",http://user:pass@proxy.example.com:8080,Secondary account with proxy
```

---

## 11. Results & Data Formats

### `data/results.csv` Format
Includes the `account` column identifying which mobile account extracted each price:
```csv
run_id,timestamp,account,route_id,source,stops,destination,status,reason,ride_name,current_price,original_price,currency,price_value,original_price_value,pickup_place_id,destination_place_id
batch-20261005-161006,2026-10-05T16:11:00,Account_1,1,Brickell City Centre,,Kaseya Center,SUCCESS,,UberX,$27.94,,USD,27.94,,ChIJ9b849YC32YgR612tUl-KdyM,ChIJlZFyCaC22YgRbFtPKFIFrRM
```

### Multi-Stop Routes in `data/routes.csv`
Multi-stop routes can be defined in `data/routes.csv` by listing pipe-separated stops in the `stops` column:
```csv
route_id,source,destination,category,stops
13,Fontainebleau,Brickell,multi_stop,Prime 112|E11EVEN
```

---

## 12. CLI Command Summary

| Command | Description |
| :--- | :--- |
| `python uber_prices.py status` | View last used account, last rotation time, completion progress, active accounts, and system RAM. |
| `python uber_prices.py batch --rotate` | Run full batch extracting prices with automatic account rotation. |
| `python uber_prices.py batch --account <Name>` | Run batch specifically using `<Name>` account without rotating. |
| `python uber_prices.py accounts list` | List registered mobile accounts, user agents, proxies, and profile folders. |
| `python uber_prices.py accounts setup --account <Name>` | Open visible browser to log in and save session cookies for an account. |
| `python uber_prices.py accounts check` | Cross-check account session profile directories and cookie databases. |
| `python uber_prices.py accounts set-proxy --account <Name> --proxy <ProxyURL>` | Set or clear proxy for a specific account. |
| `python uber_prices.py prices --pickup ... --destination ...` | Extract prices for a single route (supports `--stops` and `--method ui`). |

---

## 13. Safety, Privacy & Terms

* **Confidentiality:** Never commit `browser_profile_account*`, `data/accounts.csv`, `data/results.csv`, or `data/results/` to source control.
* **Terms of Service:** This repository is for educational and research purposes. Users assume all risk associated with automated web interaction.
