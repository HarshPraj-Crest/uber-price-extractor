# Diagnostic: run ONE route through the unchanged batch code path (run_route) with network/field logging.
# Read-only. Usage: python data/diagnose_route.py 4
import json
import sys
import time

sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

import uber_prices as up

route_ids = [int(x) for x in sys.argv[1:]]
cfg = json.load(open("routes.json", encoding="utf-8"))
t0 = time.perf_counter()
events = []


def on_response(resp):
    url = resp.url
    if "graphql" in url:
        try:
            op = (resp.request.post_data_json or {}).get("operationName")
        except Exception:
            op = "?"
        try:
            body = resp.text()
            err = "ERRORS " + body[:160] if '"errors"' in body else f"{len(body)}B"
        except Exception:
            err = "no-body"
        events.append((round(time.perf_counter() - t0, 1), resp.status, op, err))
    elif resp.status >= 400 and "mixpanel" not in url:
        events.append((round(time.perf_counter() - t0, 1), resp.status, url[:90], ""))


orig_read = up.read_suggestions


def traced_read(page, query):
    # Observe (not change) the field state around the suggestion wait.
    field = page.locator('[role="combobox"][aria-expanded="true"]')
    before = field.first.input_value() if field.count() else "<no expanded combobox>"
    print(f"  [diag {time.perf_counter() - t0:5.1f}s] waiting suggestions for {query!r}; field now {before!r}")
    try:
        return orig_read(page, query)
    finally:
        vals = [c.input_value() for c in page.get_by_role("combobox", name="Search for a location").all()]
        print(f"  [diag {time.perf_counter() - t0:5.1f}s] after wait: field values {vals}, url {page.url[:70]}")


up.read_suggestions = traced_read

with up.PlaywrightWorker(up.BrowserConfig(profile_dir=up.PROFILE_DIR, default_profile_dir=up.PROFILE_DIR)) as worker:
    ctx, page = worker.context, worker.page
    page.on("response", on_response)
    up.attach_search_monitor(page)
    records = []
    t = time.perf_counter()
    up.ensure_logged_in(page)
    print(f"session check {time.perf_counter() - t:.1f}s")
    for n, rid in enumerate(route_ids, 1):
        route = next(r for r in cfg["routes"] if r["id"] == rid)
        events.append((round(time.perf_counter() - t0, 1), "----", f"ROUTE {rid} START", ""))
        rec = up.run_route(page, route, cfg["locations"], cfg.get("region"))
        rec["resources"] = up.BrowserMonitor().snapshot(ctx, page)
        up.print_route_record(rec, n, len(route_ids))
        print(f"  open pages: {len(ctx.pages)}; cookies: {len(ctx.cookies())}")
        records.append(rec)
    out = f"data/results/diag-{time.strftime('%Y%m%d-%H%M%S')}-routes-{'-'.join(map(str, route_ids))}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"\nrecords saved: {out}")
    print("\nnetwork (graphql operations + HTTP errors):")
    for e in events:
        print("  ", e)
