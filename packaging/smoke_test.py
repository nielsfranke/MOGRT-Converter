"""Smoke test for the bundled app: start it, open a template, play it (prefetch on worker processes).

    python packaging/smoke_test.py            # finds the app in dist/
    python packaging/smoke_test.py --gui      # like a double-click on a .mogrt (native window)

Fails if the server stops answering, if not all frames get rendered ahead, or if a worker
process starts its own app instance (it would open a second server).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from sample_mogrt import make  # noqa: E402


def find_exe() -> Path:
    dist = ROOT / "dist"
    if sys.platform == "darwin":
        return dist / "MOGRT Converter.app" / "Contents" / "MacOS" / "MOGRT Converter"
    if sys.platform == "win32":
        return dist / "MOGRT Converter" / "MOGRT Converter.exe"
    return dist / "MOGRT Converter" / "mogrt-converter"


def post(base: str, path: str, body: dict, timeout: float = 10) -> bytes:
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def alive(base: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=timeout):
            return True
    except Exception:
        return False


def children(pid: int) -> list[str]:
    """Command lines of all descendant processes (diagnostics)."""
    try:
        if sys.platform == "win32":
            ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json")
            procs = json.loads(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout)
            rows = [(p["ProcessId"], p["ParentProcessId"], p.get("CommandLine") or "") for p in procs]
        else:
            out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
            rows = []
            for line in out.splitlines():
                a, b, c = line.strip().split(None, 2) + [""] * (3 - len(line.strip().split(None, 2)))
                rows.append((int(a), int(b), c))
    except Exception as e:
        return [f"(process list unavailable: {e})"]
    found, todo = [], [pid]
    while todo:
        p = todo.pop()
        for cpid, ppid, cmd in rows:
            if ppid == p:
                found.append(f"{cpid}: {cmd}")
                todo.append(cpid)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", nargs="?", type=Path, default=None)
    ap.add_argument("--gui", action="store_true", help="open the template like a double-click (native window)")
    ap.add_argument("--timeout", type=float, default=240)
    args = ap.parse_args()
    exe = args.exe or find_exe()
    tmp = Path(tempfile.mkdtemp(prefix="mogrt-smoke-"))
    sample = make(tmp / "Smoke Test.mogrt", duration=2.0)
    env = dict(os.environ, MOGRT_DATA_DIR=str(tmp / "data"), MOGRT_CACHE=str(tmp / "cache"))
    port = 8765 if args.gui else 8799
    cmd = [str(exe), str(sample)] if args.gui else [str(exe), "app", "--no-browser", "--port", str(port)]
    print("start:", cmd, flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=open(tmp / "app.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    ok = False
    try:
        t0 = time.time()
        while not alive(base, 2):
            if proc.poll() is not None or time.time() - t0 > 90:
                raise SystemExit(f"app did not start (exit code {proc.poll()})")
            time.sleep(1)
        print(f"server up after {time.time() - t0:.1f} s", flush=True)
        info = json.loads(post(base, "/api/open", {"path": str(sample)}, timeout=60))
        count = round(info["duration"] * info["fps"])
        state = {"path": str(sample), "values": {}, "duration": None, "motion": None, "scale": 0.25}
        assert post(base, "/api/frame", {**state, "t": 1.0}, timeout=60)[:4] == b"\x89PNG"
        key = json.loads(post(base, "/api/prefetch", {**state, "frame": 0}, timeout=30))["key"]
        t0 = time.time()
        done = 0
        while time.time() - t0 < args.timeout:
            s = time.time()
            if not alive(base, 10):
                raise SystemExit(f"server stopped answering after {time.time() - t0:.1f} s of prefetching")
            if time.time() - s > 3:
                print(f"warning: status took {time.time() - s:.1f} s", flush=True)
            done = len(json.loads(post(base, "/api/prefetch/status", {"key": key}))["frames"])
            if done >= count:
                break
            time.sleep(1)
        kids = children(proc.pid)
        print(f"prefetched {done}/{count} frames in {time.time() - t0:.1f} s; {len(kids)} child processes:", flush=True)
        for k in kids:
            print("  ", k[:220])
        if done < count:
            raise SystemExit("prefetch did not finish")
        # a worker that boots the whole app would start its own server: there must be exactly one app instance
        apps = [k for k in kids if "--multiprocessing-fork" not in k and "spawn_main" not in k
                and "resource_tracker" not in k and ("MOGRT Converter" in k or "mogrt-converter" in k)]
        if apps:
            raise SystemExit(f"worker processes started the app itself: {apps}")
        assert alive(base), "server not answering after prefetch"
        ok = True
        print("SMOKE TEST OK")
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        if not ok:
            print("---- app log ----")
            print((tmp / "app.log").read_text(errors="replace")[-5000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
