"""Leak smoke test for every Jev dashboard (2026-10-23). For each dashboard in the supervisor REGISTRY: load "/", discover the API paths its page calls (fetch("/...")), request each one
N times and count the process's open files before/after. An endpoint that leaves >= 3 extra descriptors per 15 calls is reported as a LEAK (what the Coin Desk /live endpoint did).
`python fdsmoke.py` (all)   `python fdsmoke.py 8801` (one port)   `--n 25` repetitions.   Supervisor runs it nightly and alerts on any leak. Needs the dashboard on 127.0.0.1 (no login there)."""
import os, re, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def pid_of(port):
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return int(r.stdout.split()[0]) if r.stdout.split() else None


def fds(pid):
    r = subprocess.run(["lsof", "-n", "-P", "-p", str(pid)], capture_output=True, text=True, timeout=20)
    return len(r.stdout.splitlines()) - 1


def get(port, path, timeout=10):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def paths_for(port):
    st, body = get(port, "/")
    if st == 401:
        return None, []
    html = body.decode("utf-8", "ignore")
    found = sorted({m.group(1) for m in re.finditer(r"""fetch\(\s*[`"'](/[A-Za-z0-9_\-./]*)""", html)})
    return st, ["/"] + [p for p in found if not p.endswith((".png", ".js", ".css", ".svg"))][:40]


def smoke(port, n=15):
    pid = pid_of(port)
    if not pid:
        return {"port": port, "error": "not listening"}
    st, paths = paths_for(port)
    if st is None:
        return {"port": port, "pid": pid, "error": "login required on localhost: skipped"}
    leaks, rows = [], []
    for p in paths:
        get(port, p)                                                   # warm-up: first call may open long-lived handles legitimately
        time.sleep(1.5)
        base = fds(pid)
        series = []
        for _ in range(3):                                             # 3 rounds: a real leak keeps growing every round; noise does not
            for _ in range(n):
                get(port, p)
            time.sleep(2.0)                                            # let sockets and GC settle before counting
            series.append(fds(pid) - base)
        time.sleep(6.0)                                                # final settle: background refresh threads and closing sockets finish; only what is STILL open counts
        settled = fds(pid) - base
        rows.append((p, series + [settled]))
        if settled >= 3 and series[0] <= series[1] <= series[2] and series[2] > series[0]:
            leaks.append((p, settled))
    return {"port": port, "pid": pid, "paths": len(paths), "leaks": leaks, "fds": fds(pid)}


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 15
    ports = [int(a) for a in sys.argv[1:] if a.isdigit() and int(a) > 1000]
    if not ports:
        import supervisor
        ports = sorted({r[3] for r in supervisor.REGISTRY if r[3]})
    bad = 0
    for p in ports:
        r = smoke(p, n)
        if r.get("error"):
            print(f":{p}  {r['error']}")
        else:
            print(f":{p}  {r['paths']} endpoints, {r['fds']} fds now  " + ("LEAKS: " + ", ".join(f"{a} (+{b})" for a, b in r["leaks"]) if r["leaks"] else "no leaks"))
            bad += bool(r["leaks"])
    sys.exit(1 if bad else 0)
