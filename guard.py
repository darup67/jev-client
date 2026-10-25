#!/usr/bin/env python3
"""Stray-process guard (called by supervisor.py every 60 s; also runnable alone). Born from the 2026-10-16 incident: a leftover `yes` ran for 21 hours in a Terminal tab burning up to 30% of a core and nobody noticed.
Scope: only processes owned by this user. Never touches root/system processes, never touches anything on the ALLOW list below.
  KILL    (SIGTERM, then SIGKILL after 3 s) known pointless CPU burners: `yes`, `cat|dd|head ... /dev/zero|/dev/urandom`, `stress`/`stress-ng`, shell `while true` loops that burn CPU.
  ALERT   (never kill) a process that has used >= 70% of a core for >= 15 min and is not allow-listed; two copies of the same Jev agent; an orphaned scratch job (parent = launchd, started from /tmp or a scratchpad) older than 2 h using CPU;
          sustained system load (1-minute load average above 3x the core count on two checks 5 minutes apart).
Every action is appended to data/stray_guard.jsonl and to Jev HQ's control log (guard entries), and alerts go through the supervisor's notifier (email, or a sounding Mac notification if Gmail is down).
  guard.py            one pass, act     guard.py --dry   report only     guard.py --status   last findings"""
import json, os, re, signal, subprocess, sys, time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "stray_guard.jsonl")
UID = os.getuid()
NCPU = os.cpu_count() or 8
# scheduled batch jobs that legitimately run hot for a long time: (pattern, seconds above 70 % before it counts as a runaway)
BATCH = [(re.compile(r"lab/run\.py"), 2700)]

ALLOW = re.compile(r"(Claude|claude|Google Chrome|Chrome Helper|WindowServer|kernel_task|Safari|WebKit|Xcode|Simulator|Terminal|iTerm|Slack|Zoom|Spotlight|mds|corespotlightd|Dock|Finder|backupd|"
                   r"TradingView|Cursor|Code Helper|Electron|ollama|tailscale|Tailscale|Chargeberry|Siri|Mail|Notes|Preview|Keynote|Numbers|Pages|Photos|Music|Podcasts)")
KILL = [(re.compile(r"^(/usr/bin/)?yes(\s|$)"), "yes: prints forever, no legitimate use as a background job", 30),
        (re.compile(r"\b(cat|dd|head|tail)\b.*\b/dev/(zero|urandom)\b"), "reads /dev/zero or /dev/urandom forever", 30),
        (re.compile(r"(^|/)stress(-ng)?(\s|$)"), "stress test left running", 120),
        (re.compile(r"\bwhile\s+(true|:|\[ *1 *\])\b.*\bdo\b"), "shell busy loop", 600)]


def ps():
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,uid=,pcpu=,etime=,rss=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        p = line.split(None, 6)
        if len(p) < 7:
            continue
        try:
            et = p[4]
            d, _, hms = et.rpartition("-")
            parts = [int(x) for x in hms.split(":")]
            secs = sum(v * m for v, m in zip(reversed(parts), (1, 60, 3600))) + (int(d) * 86400 if d else 0)
            rows.append({"pid": int(p[0]), "ppid": int(p[1]), "uid": int(p[2]), "cpu": float(p[3]), "secs": secs, "rss_mb": int(p[5]) // 1024, "cmd": p[6]})
        except ValueError:
            continue
    return rows


def record(kind, detail, **kw):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    row = {"t": time.time(), "kind": kind, "detail": detail, **kw}
    with open(LOG, "a") as f:
        f.write(json.dumps(row) + "\n")
    try:                                                                  # show it in Jev HQ's control log too
        sys.path.insert(0, HERE)
        import control
        control.audit("guard", kind, detail[:140])
    except Exception:
        pass


def kill(pid):
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(3)
        os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False
    return True


def scan(act=True, notify=None, state=None):
    state = state if state is not None else {}
    rows = [r for r in ps() if r["uid"] == UID and r["pid"] != os.getpid()]
    found = []
    for r in rows:
        base = r["cmd"].split()[0]
        for rx, why, min_s in KILL:
            if rx.search(r["cmd"]) and r["secs"] >= min_s and not ALLOW.search(base):
                found.append(("kill", r, why))
                break
    # runaway: heavy and old, not allow-listed, not already handled by the kill list
    killed = {r["pid"] for k, r, _ in found}
    hot = state.setdefault("hot", {})                                  # pid -> first time we saw it above 70%; cleared when it drops below 50%, so a busy burst in an old process is not a runaway
    now = time.time()
    live = {str(r["pid"]) for r in rows}
    for k in [k for k in hot if k not in live]:
        hot.pop(k)
    for r in rows:
        if r["pid"] in killed or ALLOW.search(r["cmd"].split()[0]) or ALLOW.search(r["cmd"][:200]):
            continue
        k = str(r["pid"])
        if r["cpu"] < 50:
            hot.pop(k, None)
            continue
        if r["cpu"] >= 70:
            hot.setdefault(k, now)
        lim_ = next((s_ for rx_, s_ in BATCH if rx_.search(r["cmd"])), 900)
        if k in hot and now - hot[k] >= lim_:
            found.append(("runaway", r, f"{r['cpu']:.0f}% of a core, above 70% for {int((now - hot[k]) // 60)} min" + (" (batch job over its own limit)" if lim_ != 900 else "")))
    # orphaned scratch jobs
    for r in rows:
        if r["ppid"] == 1 and r["secs"] > 7200 and r["cpu"] > 10 and re.search(r"(/tmp/|/private/tmp/|scratchpad|nohup)", r["cmd"]) and not ALLOW.search(r["cmd"][:200]):
            found.append(("orphan", r, f"orphaned scratch job, {r['secs'] // 3600} h old, {r['cpu']:.0f}% CPU"))
    # duplicate Jev agents (same script, same working copy)
    seen = {}
    for r in rows:
        m = re.search(r"(/Users/\w+/(?:jev|market)[\w-]*/[\w./-]*?\.py)\s+(run|\S*)", r["cmd"])
        if m and r["ppid"] == 1:
            seen.setdefault(m.group(0), []).append(r)
    for key, rs in seen.items():
        if len(rs) > 1:
            found.append(("duplicate", rs[0], f"{len(rs)} copies of {key} are running"))
    # sustained load
    load1 = os.getloadavg()[0]
    if load1 > 3 * NCPU:
        n = state.get("high_load", 0) + 1
        state["high_load"] = n
        if n >= 2:
            top = sorted(rows, key=lambda r: -r["cpu"])[:5]
            found.append(("load", {"pid": 0, "cmd": "system", "cpu": 0, "secs": 0}, f"load average {load1:.0f} on {NCPU} cores; top: " + ", ".join(f"{t['cmd'].split('/')[-1][:28]} {t['cpu']:.0f}%" for t in top)))
    else:
        state["high_load"] = 0
    out = []
    for kind, r, why in found:
        item = {"kind": kind, "pid": r["pid"], "cmd": r["cmd"][:120], "why": why}
        if kind == "kill":
            if act:
                ok = kill(r["pid"])
                item["done"] = "killed" if ok else "could not kill"
                record("killed", f"{r['cmd'][:60]} (pid {r['pid']}, {r['secs'] // 60} min): {why}", pid=r["pid"])
                if notify:
                    notify(f"guard-kill-{r['pid']}", f"Stray process stopped: {r['cmd'].split()[0].split('/')[-1]}", f"{r['cmd'][:200]}\npid {r['pid']} ran {r['secs'] // 60} min at {r['cpu']:.0f}% CPU.\nReason: {why}\nSee ~/jev-client/guard.py", 24)
            else:
                item["done"] = "would kill"
        else:
            key = f"guard-{kind}-{r['pid']}"
            if act and notify:
                notify(key, f"{kind}: {r['cmd'].split()[0].split('/')[-1][:30]}" if kind != "load" else "System load is high", f"{why}\n{r['cmd'][:200]}\npid {r['pid']}\nThe guard only alerts for this kind; nothing was stopped.", 6)
                record(kind, f"{r['cmd'][:60]}: {why}", pid=r["pid"]) if time.time() - state.get(key, 0) > 6 * 3600 else None
                state[key] = state.get(key, time.time())
            item["done"] = "alert"
        out.append(item)
    state["last"] = {"t": time.time(), "findings": out, "load1": round(load1, 1)}
    return out


if __name__ == "__main__":
    if "--status" in sys.argv:
        try:
            for l in open(LOG).read().splitlines()[-10:]:
                print(l)
        except OSError:
            print("no guard actions yet")
    else:
        res = scan(act="--dry" not in sys.argv)
        print(json.dumps(res, indent=1) if res else "nothing stray")
