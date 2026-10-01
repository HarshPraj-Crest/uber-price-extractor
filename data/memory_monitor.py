# Read-only memory sampler for the batch run (measurement only; does not touch the batch).
# Usage: python data/memory_monitor.py <out.csv> [interval_seconds]
# Waits for the `uber_prices.py batch` process, then samples it and its whole process tree
# (python -> playwright driver node.exe -> chrome.exe ...) until it exits.
import csv
import sys
import time
from datetime import datetime

import psutil

OUT = sys.argv[1]
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
MB = 1024 * 1024


def find_batch_root():
    for p in psutil.process_iter(["cmdline", "ppid"]):
        cmd = " ".join(p.info["cmdline"] or [])
        if "uber_prices.py" in cmd and " batch" in cmd:
            try:  # the venv launcher spawns the real interpreter with the same cmdline: take the outermost
                parent = psutil.Process(p.info["ppid"])
                if "uber_prices.py" in " ".join(parent.cmdline()):
                    continue
            except psutil.Error:
                pass
            return p
    return None


root = None
print("waiting for batch process...", flush=True)
while root is None:
    root = find_batch_root()
    time.sleep(0.5)
print(f"monitoring pid {root.pid}", flush=True)

fields = ["time", "sys_total_mb", "sys_available_mb", "sys_used_pct",
          "python_procs", "python_rss_mb", "python_private_mb", "python_uss_mb",
          "driver_procs", "driver_rss_mb", "driver_private_mb", "driver_uss_mb",
          "chrome_procs", "chrome_rss_mb", "chrome_private_mb", "chrome_uss_mb",
          "tree_private_mb", "tree_uss_mb"]
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    while root.is_running() and root.status() != psutil.STATUS_ZOMBIE:
        vm = psutil.virtual_memory()
        groups = {"python": [0, 0, 0, 0], "driver": [0, 0, 0, 0], "chrome": [0, 0, 0, 0]}
        try:
            procs = [root] + root.children(recursive=True)
        except psutil.Error:
            break
        for p in procs:
            try:
                name = p.name().lower()
                key = "chrome" if "chrome" in name else "driver" if "node" in name else "python" if "python" in name else None
                if key is None:
                    continue
                mi = p.memory_full_info()
                g = groups[key]
                g[0] += 1
                g[1] += mi.rss
                g[2] += getattr(mi, "private", mi.rss)
                g[3] += mi.uss
            except psutil.Error:
                pass
        row = {"time": datetime.now().isoformat(timespec="seconds"),
               "sys_total_mb": round(vm.total / MB), "sys_available_mb": round(vm.available / MB),
               "sys_used_pct": vm.percent}
        for key, (n, rss, priv, uss) in groups.items():
            row.update({f"{key}_procs": n, f"{key}_rss_mb": round(rss / MB, 1),
                        f"{key}_private_mb": round(priv / MB, 1), f"{key}_uss_mb": round(uss / MB, 1)})
        row["tree_private_mb"] = round(sum(g[2] for g in groups.values()) / MB, 1)
        row["tree_uss_mb"] = round(sum(g[3] for g in groups.values()) / MB, 1)
        w.writerow(row)
        f.flush()
        time.sleep(INTERVAL)
print("batch process ended; monitor stopped", flush=True)
