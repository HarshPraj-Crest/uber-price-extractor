# Uber Price Extractor

A read-only Python and Playwright proof-of-concept for extracting ride prices from Uber's web interface for fixed point-to-point routes in Miami/South Florida.

---

## 1. Project Overview

### What It Does
This project automates the sequential extraction of ride prices (e.g. UberX, UberXL, Black SUV) across a list of predefined pickup and destination routes in Miami using Python, Playwright, and a persistent browser session.

### Hard Rules & Constraints
* **Never requests or books rides:** The tool stops once ride options and prices are rendered. No ride requests, payment submissions, or booking buttons are ever clicked.
* **No automated login or MFA/CAPTCHA solving:** Login must be performed manually by a human user in a visible browser. Session cookies are persisted locally to remain logged in.
* **No bot evasion or spoofing:** Operates transparently using a real Google Chrome / Chromium browser instance with explicit user interaction timing.

---

## 2. Workflow

```
+------------------+
| data/routes.csv  |  (Input: route_id, source, destination)
+--------+---------+
         |
         v
+------------------+
|  batch command   |  (uber_prices.py batch)
+--------+---------+
         |
         v
+------------------------+
|   PlaywrightWorker     |  (Launches Chrome/Chromium + persistent browser profile)
+--------+---------------+
         |
         v
+------------------------+
|  Check Session Status  |  (Redirects to auth -> stops with AUTH_REQUIRED)
+--------+---------------+
         |
         v
+-----------------------------------------------------------------------------------+
| For each route:                                                                   |
|  1. Select & verify pickup place (LocationSpec exact name & address match)        |
|  2. Select & verify destination place (LocationSpec exact match)                  |
|  3. Validate South Florida region bounding box (lat/lng check)                    |
|  4. Open Uber route URL (/go/drop?pickup=...&drop[0]=...)                         |
|  5. Wait for stable priced ride options list                                      |
|  6. Extract ride names, prices, and original prices                               |
+--------+--------------------------------------------------------------------------+
         |
         v
+-----------------------------------------------------------------------------------+
| Output Generation:                                                                |
|  - Append ride rows / failure status to data/results.csv                          |
|  - Write data/results/batch-<timestamp>.json                                      |
|  - Write data/results/batch-<timestamp>-performance.json                          |
+-----------------------------------------------------------------------------------+
```

### Why Location Selection is Strict
Uber autocomplete suggestions can yield ambiguous results or distant locations with similar names. The extractor enforces strict location verification (`match_suggestion` in `uber_prices.py`):
* The suggestion name must match the expected place name or an allowed alias.
* All configured address hint words must exist as whole words in the suggestion address.
* The resolved coordinates must fall inside the defined geographic bounding box (`region` in `routes.json`).
* If no suggestion matches or if multiple suggestions match, the process halts location selection and raises `LOCATION_AMBIGUOUS` (or `LocationNotFoundError`) rather than clicking an unverified suggestion.

---

## 3. Folder Structure

```
uber-price-extractor/
├── uber_prices.py                 # Main CLI entry point (subcommands, batch execution, location matching)
├── browser_worker.py              # Playwright browser lifecycle wrapper (persistent profile launch & cleanup)
├── routes.json                    # Location lookup mapping (names, address hints, aliases) and region bounding box
├── requirements.txt               # Python package dependencies
├── .gitignore                     # Git exclusion rules for private profiles, logs, and generated results
├── data/
│   ├── routes.csv                 # Route input definition file (route_id, source, destination, etc.)
│   ├── accounts.csv               # [Generated locally] Account profile metadata (phone, profile path, user agent)
│   ├── results.csv                # [Generated locally / Git-ignored] Cumulative CSV output containing extracted prices
│   ├── results/                   # [Generated locally / Git-ignored] Per-batch JSON and performance summary files
│   ├── screenshots/               # [Generated locally / Git-ignored] Failure & diagnostic screenshots
│   ├── diagnose_route.py          # Diagnostic script to trace network GraphQL responses for single routes
│   └── memory_monitor.py          # Standalone RSS memory sampler process for batch runs
└── browser_profile/               # [Generated locally / CONFIDENTIAL / Git-ignored] Persistent Chrome session profile
```

### Confidentiality and Privacy Notes
* `browser_profile/` (and any account profiles like `browser_profile_account1/`) contains live session cookies and authentication tokens. It is **confidential** and must **never** be committed to source control.
* `data/results.csv`, `data/results/`, `data/accounts.csv`, and `data/screenshots/` are generated locally during execution and are ignored by `.gitignore`.

---

## 4. Requirements & Setup (Windows)

### Prerequisites
* Windows 10 / 11
* Python 3.9+ installed and available in PATH
* Google Chrome installed (optional, standalone Chromium bundled with Playwright is supported)

### Step 1: Create Virtual Environment
Open PowerShell or Command Prompt in the project directory:

```cmd
python -m venv .venv
```

### Step 2: Install Dependencies
Activate the environment and install required packages:

```cmd
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Step 3: Install Playwright Chromium
Install the Playwright browser binaries:

```cmd
.\.venv\Scripts\python.exe -m playwright install chromium
```

---

## 5. First-Time Login

Before running automated extraction, you must establish an authenticated session.

### Step 1: Open the Browser in Interactive Mode

```cmd
.\.venv\Scripts\python.exe uber_prices.py open
```

This launches a visible Google Chrome/Chromium window using the persistent profile directory (`browser_profile/`).

### Step 2: Perform Manual Authentication
1. In the opened browser window, navigate to Uber's login screen if not redirected automatically.
2. Enter your phone number and complete the OTP / MFA verification manually.
3. Once you reach the Uber home screen (`https://m.uber.com/go/home`) and verify you are logged in, return to your terminal and press `Enter`.

### Step 3: Verify Saved Session

```cmd
.\.venv\Scripts\python.exe uber_prices.py check-session
```

If successful, the command outputs `[ok] Session is logged in.` The authenticated session is stored in `browser_profile/` and will be reused for subsequent batch runs.

---

## 6. How to Run

All commands can be executed using `.\.venv\Scripts\python.exe`:

### Check Session
Verifies if the saved profile session is still valid and logged in.
```cmd
.\.venv\Scripts\python.exe uber_prices.py check-session
```

### Open Browser
Launches the visible browser instance for initial manual login or inspection.
```cmd
.\.venv\Scripts\python.exe uber_prices.py open
```

### Browser Environment Info
Prints Playwright version, detected browser binary executable, engine, User-Agent, and profile directory.
```cmd
.\.venv\Scripts\python.exe uber_prices.py browser-info
```

### Run Batch Price Extraction
Runs all routes in `data/routes.csv` sequentially in a single browser session.
```cmd
.\.venv\Scripts\python.exe uber_prices.py batch
```

### Run Specific Routes Only
Runs only specific numeric route IDs from `data/routes.csv`.
```cmd
.\.venv\Scripts\python.exe uber_prices.py batch --only 1,2,5
```

### Run Batch with Custom Paths
Specifies custom paths for input CSV, output CSV, or location JSON mapping.
```cmd
.\.venv\Scripts\python.exe uber_prices.py batch --input data/routes.csv --output data/results.csv --routes routes.json
```

### Single Route Price Extraction
Runs price extraction for a single pickup and destination without updating `data/results.csv`.
```cmd
.\.venv\Scripts\python.exe uber_prices.py prices --pickup "Brickell City Centre" --destination "Kaseya Center"
```

### Test Location Autocomplete Match
Tests autocomplete suggestion resolution for pickup and destination without loading ride prices.
```cmd
.\.venv\Scripts\python.exe uber_prices.py locations --pickup "Brickell City Centre" --destination "Kaseya Center"
```

### Test Route Search Navigation
Fills pickup and destination locations, clicks Search, and keeps the browser window open.
```cmd
.\.venv\Scripts\python.exe uber_prices.py search --pickup "Brickell City Centre" --destination "Kaseya Center"
```

### Display Help Information
Shows all CLI flags, commands, and global options.
```cmd
.\.venv\Scripts\python.exe uber_prices.py --help
```

### Global Browser Options
Global options can be passed to select browser engines or custom profiles:
* `--browser chrome|chromium|firefox|webkit`: Selects the browser engine (default: `chromium`).
* `--profile-dir PATH`: Custom persistent profile folder path (default: `browser_profile/`).
* `--account NAME`: Account name from `data/accounts.csv`.

> [!WARNING]
> Do not pass `--browser firefox` or `--browser webkit` with the default `browser_profile/` folder. The default profile format is specific to Chromium/Chrome; using other engines on a Chrome profile will cause errors.

---

## 7. Input Format

### `data/routes.csv` Columns

| Column Header | Status | Description |
| :--- | :--- | :--- |
| `route_id` | **Required** | Unique integer identifier for the route. |
| `source` | **Required** | Pickup location label (must exist in `routes.json`). |
| `destination` | **Required** | Destination location label (must exist in `routes.json`). |
| `category` | Optional | Route classification string (e.g. `short`, `long`, `airport_pickup`). |
| `stops` | Optional | Pipe-separated list of intermediate stops (e.g. `Stop A\|Stop B`). Multi-stop rows are automatically skipped by the batch processor (`SKIPPED_MULTI_STOP`). |
| `distance_mi` | Reference Only | Estimated distance in miles (not used by scraper logic). |
| `duration_min` | Reference Only | Estimated duration in minutes (not used by scraper logic). |
| `notes` | Reference Only | Freeform descriptive notes (not used by scraper logic). |

### `routes.json` Structure
Maps source and destination text labels from `data/routes.csv` to exact Uber autocomplete search strings and address validation rules:

```json
{
  "region": {
    "name": "South Florida",
    "min_lat": 25.2, "max_lat": 27.0,
    "min_lng": -80.9, "max_lng": -79.9
  },
  "locations": {
    "Brickell City Centre": {
      "name": "Brickell City Centre",
      "address": ["South Miami Avenue", "FL"]
    },
    "Kaseya Center": {
      "name": "Kaseya Center",
      "address": ["Biscayne Boulevard", "FL"]
    }
  }
}
```

#### Adding a New Route and Location
1. **Add Location Mapping:** In `routes.json`, add an entry under `"locations"` where key matches your label, `"name"` is the exact text to type into Uber, and `"address"` contains required substring keywords.
2. **Add CSV Row:** In `data/routes.csv`, append a row with a new `route_id`, matching `source`, and `destination`.

---

## 8. Output

### Console Output Example
```
[BATCH] batch-YYYYMMDD-HHMMSS: 2 routes (2 pickup->destination, 0 multi-stop will be skipped)
[BROWSER] Launch: 2.1s
[SESSION] Logged in: 1.5s

[ROUTE 01/2] #1 Brickell City Centre -> Kaseya Center
  Location selection: 8.2s
  Route form:         2.1s
  Search/navigation:  3.4s
  Price extraction:   2.0s
  Total:              15.7s
  Selected: Brickell City Centre (701 S Miami Ave, Miami, FL)
        ->  Kaseya Center (601 Biscayne Blvd, Miami, FL)
  Prices: 3  UberX $12.34, UberXL $18.50, Black $45.00

[BATCH SUMMARY]
Routes in file:        2  (skipped multi-stop: 0, not run: 0, config errors: 0)
Routes attempted:      2
Successful:            2
Failed:                0
Total time:            0 min 35 sec
Browser launch:        2.1s   Session check: 1.5s   Close: 0.8s
Average route time:    15.7 sec
Total prices:          6  (avg 3 per successful route)
Results:     data/results/batch-YYYYMMDD-HHMMSS.json
Performance: data/results/batch-YYYYMMDD-HHMMSS-performance.json
CSV:         data/results.csv (appended)
```

### `data/results.csv` Format

All results from batch runs are appended to `data/results.csv`. Previous history is preserved.

#### Example Rows (CSV)
```csv
run_id,timestamp,route_id,source,destination,status,reason,ride_name,current_price,original_price,currency,price_value,original_price_value,pickup_place_id,destination_place_id
batch-YYYYMMDD-HHMMSS,2026-10-01T12:00:00,1,Brickell City Centre,Kaseya Center,SUCCESS,,UberX,$12.34,,USD,12.34,,place_id_fake_123,place_id_fake_456
batch-YYYYMMDD-HHMMSS,2026-10-01T12:00:00,1,Brickell City Centre,Kaseya Center,SUCCESS,,UberXL,$18.50,,USD,18.50,,place_id_fake_123,place_id_fake_456
batch-YYYYMMDD-HHMMSS,2026-10-01T12:01:00,2,Fontainebleau Miami Beach,LIV Nightclub,LOCATION_AMBIGUOUS,No Uber suggestion exactly matches 'Invalid Place',,,,,,,,
```

#### File Append & Locking Behavior
* **History Preservation:** Existing file rows are never overwritten.
* **Header Validation:** If `data/results.csv` exists, the column headers are verified before writing. If columns differ, append is rejected to prevent schema corruption.
* **Excel Lock Safety:** If `data/results.csv` is open in Excel (causing a `PermissionError`), the scraper retries up to 3 times before falling back to saving JSON output without crashing the process.

### `data/results/batch-*.json` Structure
Contains structured execution details for the batch:

```json
{
  "batch_id": "batch-YYYYMMDD-HHMMSS",
  "started_at": "2026-10-01T12:00:00",
  "finished_at": "2026-10-01T12:05:00",
  "stopped_reason": null,
  "routes": [
    {
      "id": 1,
      "category": "short",
      "pickup_label": "Brickell City Centre",
      "destination_label": "Kaseya Center",
      "status": "SUCCESS",
      "result": {
        "fetched_at": "2026-10-01T12:00:15",
        "currency": "USD",
        "rides": [
          { "ride_type": "UberX", "price": "$12.34", "price_value": 12.34 }
        ]
      }
    }
  ]
}
```

### Failure Screenshots
When a route execution fails or raises an error, a diagnostic PNG image is automatically saved under `data/screenshots/<timestamp>-<route_id>-<status>.png`.

---

## 9. Statuses & Exit Codes

### Machine-Readable Status Table

| Status String | Category | Description / Cause | Action Required |
| :--- | :--- | :--- | :--- |
| `SUCCESS` | Success | Route locations resolved and ride prices extracted successfully. | None. |
| `LOCATION_AMBIGUOUS` | Location Failure | No exact match found, or multiple places matched the criteria. | Refine name or address hints in `routes.json`. |
| `SEARCH_UNAVAILABLE` | Location Failure | Uber did not return suggestion list for typed query. | Check network connection or retry route. |
| `WRONG_LOCATION` | Location Failure | Uber selected place mismatch or coordinates outside region. | Check region box or update location specs. |
| `PRICING_NOT_LOADED` | Extraction Failure | Uber ride option container failed to load prices in time. | Re-run batch or check Uber UI changes. |
| `PRICE_NOT_FOUND` | Extraction Failure | Ride container rendered but contained no numeric price strings. | Inspect saved screenshot under `data/screenshots/`. |
| `AUTH_REQUIRED` | Session Failure | Session expired or redirected to Uber authentication page. | Re-run `python uber_prices.py open` and log in. |
| `SECURITY_CHALLENGE` | Security Failure | Uber displayed a CAPTCHA or security verification prompt. | Complete CAPTCHA manually in browser. |
| `TIMEOUT` | System Failure | Page navigation or DOM locator wait exceeded timeout limit. | Inspect network latency and re-run. |
| `NETWORK_ERROR` | System Failure | Underlaying network connection dropped or failed. | Check network connectivity. |
| `BROWSER_CRASH` | System Failure | Playwright browser process closed unexpectedly. | Restart batch execution. |
| `CONFIG_ERROR` | Setup Failure | Invalid arguments, missing routes, or malformed input CSV. | Check `data/routes.csv` syntax. |
| `ERROR` | Generic Failure | Unclassified runtime exception occurred. | Check log output and stack trace. |
| `SKIPPED_MULTI_STOP` | Skip Status | Route contains intermediate stops (not supported). | None (multi-stop routes are skipped). |
| `NOT_RUN` | Batch Status | Route was skipped because batch stopped early due to hard error. | Resolve stopping condition and re-run batch. |

### Process Exit Codes
* `0`: Batch completed with 0 route failures.
* `1`: Critical error (e.g. invalid configuration, session missing, or batch terminated early).
* `2`: Batch completed, but one or more attempted routes failed.

### Batch Stopping Policy
The batch processor stops immediately and marks remaining routes `NOT_RUN` upon encountering:
1. Hard security/auth failures: `AUTH_REQUIRED`, `SECURITY_CHALLENGE`, or `BROWSER_CRASH`.
2. Two consecutive `SEARCH_UNAVAILABLE` failures (indicating systemic search degradation).

---

## 10. Troubleshooting

### 1. Session Expired (`AUTH_REQUIRED`)
* **Symptom:** Terminal output displays `[SESSION] AUTH_REQUIRED: Session expired.`
* **Fix:** Run `.\.venv\Scripts\python.exe uber_prices.py open`, enter login credentials manually, verify home page loads, press `Enter`, then run `check-session`.

### 2. `LOCATION_AMBIGUOUS` or `LocationNotFoundError`
* **Symptom:** Route fails because Uber altered suggestion text or added new search choices.
* **Fix:** Run `.\.venv\Scripts\python.exe uber_prices.py locations --pickup "..." --destination "..."` to view live suggestion output, then adjust `name` or `address` strings in `routes.json`.

### 3. `Failed to create a ProcessSingleton` / Profile Locked
* **Symptom:** Playwright fails to launch Chromium stating profile directory is in use.
* **Fix:** Ensure no other instance of Chrome or background `python uber_prices.py` process is currently running with the same `--profile-dir`.

### 4. `SEARCH_UNAVAILABLE` / Slow Page Loading
* **Symptom:** Suggestions timeout due to network delays.
* **Fix:** Verify internet connection speed and check if Uber service is experiencing outage.

---

## 11. Performance & Limits

* **Route Speed:** ~30 to 65 seconds per route (including typing pauses, DOM waits, and price stabilization).
* **Batch Duration:** Approximately 30 to 35 minutes for a full 30-route run.
* **Concurrency:** Sequential execution only in a single browser context. Multi-threading/parallel contexts are not supported.
* **Multi-stop Support:** Multi-stop routes in `data/routes.csv` (`stops` column) are skipped.
* **Cloud Hosting Note:** AWS `t2.micro` instances (1 GB RAM) are insufficient for running Playwright Chromium instances reliably. A minimum of `t3.small` or `t3.medium` (2 GB+ RAM) is recommended.

---

## 12. Safety, Privacy & Terms

### Git & Security Practices
* Never commit `browser_profile/`, `data/results.csv`, `data/results/`, `data/screenshots/`, or `.env` files to git repositories.
* Check `.gitignore` before pushing changes.
* Use a private repository if pushing to GitHub.

### Terms of Service & Risk Disclaimer
This repository is for educational and proof-of-concept purposes only. Accessing Uber via automated scripts may violate Uber's Terms of Service. Users assume all risk associated with account usage. Always maintain low execution frequencies and respect security challenge pauses.

---

## 13. Future Roadmap

- [ ] Laravel Backend Integration (database persistence for extracted prices)
- [ ] Automated Cron Scheduling Layer
- [ ] Admin Monitoring Dashboard & Web Interface
- [ ] Multi-stop route extraction support

---

## 14. Code Verification & Notes

During audit of the codebase, all features, commands, status codes, and configuration structures referenced in this document were verified directly against `uber_prices.py`, `browser_worker.py`, `routes.json`, and `data/routes.csv`.
