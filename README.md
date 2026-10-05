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

## 2. Workflow & Multi-Account Rotation

```
+---------------------------+
| data/accounts_example.csv |  (Reference template for data/accounts.csv)
+-------------+-------------+
              |
              v
+---------------------------+
|    data/accounts.csv      |  (Accounts, proxies, user-agents, profiles)
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
|  - Append result rows (including `account` name) to data/results.csv              |
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
│   ├── results.csv                # [Generated / Git-ignored] Cumulative CSV containing extracted prices & account info
│   ├── results/                   # [Generated / Git-ignored] Per-batch JSON results and performance files
│   └── screenshots/               # [Generated / Git-ignored] Failure & diagnostic screenshots
└── browser_profile_account*/      # [Generated / Confidential / Git-ignored] Persistent Chrome account session profiles
```

---

## 4. Setup & First-Time Login (Saving Session Cookies)

Before running automated extraction, you must log in and save session cookies for each account you want to use.

### Step 1: Register Accounts in `data/accounts.csv`
You can register new accounts using the CLI or reference `data/accounts_example.csv`:

```cmd
# Register Account_1
python uber_prices.py accounts add --name Account_1 --phone +15551234567

# Register Account_2
python uber_prices.py accounts add --name Account_2 --phone +15559876543
```

### Step 2: Log In & Save Cookies for the First Account (`Account_1`)
Run the setup command for `Account_1`:

```cmd
python uber_prices.py accounts setup --account Account_1
```

1. A visible Chrome window opens loading `browser_profile_account1`.
2. Enter your mobile phone number and complete the OTP / MFA verification manually on Uber's login screen.
3. Once logged in and viewing the Uber home page (`https://m.uber.com/go/home`), return to your terminal and press `Enter`.
4. All session cookies and login tokens are automatically persisted in `browser_profile_account1/`.

### Step 3: Log In & Save Cookies for Different Accounts (`Account_2`, `Account_3`...)
Repeat the setup step for each additional account:

```cmd
python uber_prices.py accounts setup --account Account_2
```

Complete the manual login in the opened browser window and press `Enter`. Its session cookies are stored separately in `browser_profile_account2/`.

### Step 4: Verify Saved Account Sessions
Cross-check all account sessions to ensure cookie databases exist:

```cmd
python uber_prices.py accounts check
```

---

## 5. Account Rotation & Checking Last Used Account

### Run Batch Extraction with Account Rotation
Automatically rotates to the next active account in `data/accounts.csv`, loads its persistent profile and assigned proxy, and runs the batch routes:

```cmd
python uber_prices.py batch --rotate
```

### Check Last Used Account & Status
To view **which account was used last**, when it was rotated, how many routes were completed/remaining, and system RAM:

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

---

## 6. Managing Proxies Per Account

You can assign HTTP or SOCKS5 proxies per account in `data/accounts.csv`:

```cmd
# Set HTTP or SOCKS5 proxy for Account_1
python uber_prices.py accounts set-proxy --account Account_1 --proxy http://user:pass@1.2.3.4:8080

# Clear proxy (use direct connection)
python uber_prices.py accounts set-proxy --account Account_1 --proxy direct
```

---

## 7. Route Execution & Multi-Stop Commands

### Extract Prices for a Single Route
```cmd
python uber_prices.py prices --pickup "Brickell City Centre" --destination "Kaseya Center"
```

### Extract Prices for a Multi-Stop Route
Supports 1 or more intermediate stops (`Pickup → Stop 1 → Stop 2 → Dropoff`):
```cmd
# Direct URL method (default):
python uber_prices.py prices --pickup "Fontainebleau" --stops "Prime 112" "E11EVEN" --destination "Brickell"

# Interactive UI method (clicks Uber's "Add stop" button in browser):
python uber_prices.py prices --pickup "Fontainebleau" --stops "Prime 112" "E11EVEN" --destination "Brickell" --method ui
```

---

## 8. Tracking Execution Time & Memory Usage

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

## 9. Reference Account Configuration File

A reference template is available at `data/accounts_example.csv`:

```csv
account_name,phone_number,profile_dir,status,last_used,user_agent,proxy,notes
Account_1,+15551234567,browser_profile_account1,active,,"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",,Primary account
Account_2,+15559876543,browser_profile_account2,active,,"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",http://user:pass@proxy.example.com:8080,Secondary account with proxy
```

---

## 10. Results & Output Data Formats

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
