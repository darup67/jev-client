#!/usr/bin/env python3
"""Jev supervisor: the fast, independent guard under every Jev agent (launchd com.dhruv.supervisor, KeepAlive, ticks every 30 s). The 15-minute watchdog (market-lab/ops/watchdog.py) stays as the second layer;
the two watch each other, so one dying cannot go unnoticed.

Every tick, for each agent in REGISTRY:
  * LOADED + RUNNING   launchd has the job and it has a pid (KeepAlive jobs only); if not -> bootstrap / kickstart.
  * FRESH              its heartbeat / output file moved within `max_age` seconds; if not -> kickstart (max 3 per hour per agent, then alert only).
  * REACHABLE          dashboards answer HTTP on their port (3 misses in a row -> kickstart).
  * QUIET              errors.jsonl: a flood of one repeating error (>= 40 in 5 min) -> an email within minutes naming the error (the 2026-10-14 Markets 422 loop ran for an hour before the old hourly count noticed).
Hourly: size-cap every agent log (copy tail to .1, truncate in place) and check disk. Nightly 02:30: online SQLite backups of every desk database to ~/Backups/jev/<date>, 7 days kept.
Alerts: email through ~/flip-notifier/email-ui, plus a Mac notification with sound if Gmail itself is down. Deduped per key.
  supervisor.py            run the loop     supervisor.py --once   one tick (no kicks beyond what a tick needs)     supervisor.py --status   last status     supervisor.py --dry   report only"""
import glob, json, os, shutil, sqlite3, subprocess, sys, time, urllib.request

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "supervisor.json")
UID = os.getuid()
DRY = "--dry" in sys.argv
P = lambda *a: os.path.join(HOME, *a)

# label, heartbeat file (mtime), max age s, port (dashboards), keepalive
REGISTRY = [
    ("com.dhruv.jevmajors.main",     P("jev-majors/data/heartbeat.json"),         150, None, True),
    ("com.dhruv.jevmajors.invert",   P("jev-majors-invert/data/heartbeat.json"),  150, None, True),
    ("com.dhruv.jevcoindesk.main",   P("jev-coin-desk/data/fomo_alive"),         120, None, True),     # FOMO lane, split out of Jev Majors 2026-10-22
    ("com.dhruv.jevcoindesk.chains", P("jev-coin-desk/data/chains_alive.json"),  120, None, True),     # multi-chain early-pool recorder, EVM chains (record only) 2026-10-22
    ("com.dhruv.jevcoindesk.chainssol", P("jev-coin-desk/data/chains_alive_sol.json"), 120, None, True),  # same, Solana pump.fun feed in its own process
    ("com.dhruv.jevcoindesk.health", P("jev-coin-desk/data/health.json"),      180, None, True),     # Coin Desk health monitor (health.py loop) 2026-10-22
    ("com.dhruv.jevcoindesk.dashboard", None,                                     0,   8801, True),
    ("com.dhruv.jevmajors.dashboard", None,                                       0,   8789, True),
    ("com.dhruv.jevmarkets.main",    P("jev-markets/data/heartbeat.json"),        150, None, True),
    ("com.dhruv.jevmarkets.dashboard", None,                                      0,   8790, True),
    ("com.dhruv.jevmarketsinvert.main", P("jev-markets-invert/data/heartbeat.json"), 150, None, True),
    ("com.dhruv.jevmarketsinvert.dashboard", None,                                0,   8797, True),
    ("com.dhruv.jevagents.main",     P("jev-agents/data/heartbeat.json"),         240, None, True),
    ("com.dhruv.jevagents.dashboard", None,                                       0,   8798, True),
    ("com.dhruv.jevcareer.dashboard", None,                                        0,   8799, True),
    ("com.dhruv.jevhome.dashboard",   None,                                        0,   8800, True),
    ("com.dhruv.jevpredict.main",    P("jev-predict/data/heartbeat.json"),        150, None, True),
    ("com.dhruv.jevpredict.dashboard", None,                                      0,   8795, True),
    ("com.dhruv.jevoptionsiv.main",  P("jev-options-iv/data/heartbeat.json"),     300, None, True),
    ("com.dhruv.jevoptionsiv.dashboard", None,                                    0,   8792, True),
    ("com.dhruv.jevshadow",          P("jev-shadow/out.log"),                     9000, None, True),
    ("com.dhruv.jevshop.watch",      P("jev-shop/data/watch_alive"),              120, None, True),
    ("com.dhruv.jevshop",            None,                                        0,   8796, True),
    ("com.dhruv.askjev",             None,                                        0,   8794, True),
]
ERROR_FILES = {"jev-majors": P("jev-majors/data/errors.jsonl"), "jev-markets": P("jev-markets/data/errors.jsonl"), "jev-predict": P("jev-predict/data/errors.jsonl"),
               "jev-options-iv": P("jev-options-iv/data/errors.jsonl"), "jev-majors-invert": P("jev-majors-invert/data/errors.jsonl"), "jev-markets-invert": P("jev-markets-invert/data/errors.jsonl"), "jev-agents": P("jev-agents/data/errors.jsonl")}
LOG_GLOBS = [P("jev-*/*.log"), P("jev-client/*.log"), P("market-lab/ops/*.log"), P("zillow-agent/*.log")]
DBS = [P("jev-coin-desk/data"), P("jev-majors/data"), P("jev-majors-invert/data"), P("jev-markets/data"), P("jev-markets-invert/data"), P("jev-agents/data"), P("jev-predict/data"), P("jev-options-iv/data"), P("jev-shadow/data"), P("jev-desk/data")]
BACKUP_ROOT = P("Backups/jev")
LOG_MAX = 20 * 1024 * 1024

st = {"alerted": {}, "kicks": {}, "misses": {}, "last_rotate": 0, "last_backup": "", "last_disk": 0, "last_guard": 0, "guard": {}}
try:
    st.update(json.load(open(STATE)))
except (OSError, ValueError):
    pass


def log(m):
    print(time.strftime("%m-%d %H:%M:%S"), m, flush=True)


def save(status):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    out = {**st, "status": status, "t": time.time()}
    with open(STATE + ".tmp", "w") as f:
        json.dump(out, f)
    os.replace(STATE + ".tmp", STATE)


def notify(key, subject, body, every_h=3):
    """Email (and a sounding Mac notification, since previews are off) once per key per every_h hours."""
    if time.time() - st["alerted"].get(key, 0) < every_h * 3600:
        return
    st["alerted"][key] = time.time()
    if DRY:
        return log(f"DRY would alert: {subject}")
    ok = False
    try:
        sys.path.insert(0, P("flip-notifier"))
        import email_ui
        ok = email_ui.send(f"Supervisor · System alert: {subject}", {"kind": "System alert", "status": {"text": "NEEDS ATTENTION", "tone": "bad"}, "title": subject,
            "subtitle": f"Jev Supervisor · {time.strftime('%a %b %-d, %-I:%M %p')} ET", "sections": [{"title": "Details", "blocks": [{"type": "code", "text": body[-3000:]}]},
            {"title": "What the supervisor has done", "blocks": [{"type": "para", "text": "It restarts stuck agents itself (3 times an hour at most) and emails only when that is not enough or the problem needs a person."}]}],
            "footer": "Sent by the Jev supervisor (~/jev-client/supervisor.py, every 30 seconds)."})
    except Exception as e:
        log(f"alert email failed: {e!r}")
    if not ok:
        subprocess.run(["osascript", "-e", f'display notification "" with title "⚠️ {subject[:90]}" sound name "Basso"'], capture_output=True)
    log(f"ALERT {'emailed' if ok else 'notified'}: {subject}")


def job(label):
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3 and p[2] == label:
            return {"loaded": True, "pid": None if p[0] == "-" else int(p[0]), "exit": p[1]}
    return {"loaded": False, "pid": None, "exit": None}


def kick(label, why):
    """Start/restart a job; at most 3 restarts per agent per hour so a crash loop cannot thrash the machine (it alerts instead)."""
    ks = [t for t in st["kicks"].get(label, []) if time.time() - t < 3600]
    if len(ks) >= 3:
        notify("loop-" + label, f"{label.split('.', 2)[-1]} keeps failing after 3 restarts this hour", f"{label}: {why}\nSee its err.log; the supervisor stopped restarting it for now.", 2)
        return False
    st["kicks"][label] = ks + [time.time()]
    if DRY:
        log(f"DRY would kick {label}: {why}")
        return True
    plist = P("Library/LaunchAgents", label + ".plist")
    if not job(label)["loaded"] and os.path.exists(plist):
        subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", plist], capture_output=True)
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{UID}/{label}"], capture_output=True, text=True)
    log(f"kick {label} ({why}): {'ok' if r.returncode == 0 else r.stderr.strip()}")
    return True


def http_ok(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=4).read(1)
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500                                   # a 401/403 login wall still means the server is up
    except Exception:
        return False


def check_agents():
    out = {}
    for label, hb, max_age, port, keep in REGISTRY:
        name = label.replace("com.dhruv.", "")
        if not os.path.exists(P("Library/LaunchAgents", label + ".plist")):
            continue
        j = job(label)
        if os.path.exists(P(os.path.dirname(os.path.dirname(hb)) if hb else ".", "STOPPED")):
            out[name] = "stopped by user"
            continue
        if not j["loaded"] or (keep and j["pid"] is None):
            kick(label, "not running")
            out[name] = "restarted (not running)"
            continue
        if hb and max_age:
            age = time.time() - os.path.getmtime(hb) if os.path.exists(hb) else None
            if age is None or age > max_age:
                if kick(label, f"heartbeat {'missing' if age is None else str(int(age)) + 's old'}"):
                    out[name] = "restarted (stale heartbeat)"
                    continue
                out[name] = "STALE (restart limit hit)"
                continue
        if port:
            ok = http_ok(port)
            st["misses"][label] = 0 if ok else st["misses"].get(label, 0) + 1
            if st["misses"][label] >= 3:
                st["misses"][label] = 0
                kick(label, f"port {port} not answering")
                out[name] = "restarted (port dead)"
                continue
        out[name] = "ok"
    return out


def check_errors():
    """A flood of one repeating error = a bug, not noise: email within minutes, naming it."""
    res = {}
    for name, f in ERROR_FILES.items():
        try:
            size = os.path.getsize(f)
            with open(f, "rb") as fh:
                fh.seek(max(0, size - 400000))
                lines = fh.read().decode("utf-8", "ignore").splitlines()[1:]
        except OSError:
            continue
        recent, now = {}, time.time()
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if now - r.get("t", 0) < 300:
                key = f"{r.get('where', '?')}: {str(r.get('err') or r.get('error') or r.get('msg'))[:110]}"
                recent[key] = recent.get(key, 0) + 1
        top = max(recent.items(), key=lambda kv: kv[1]) if recent else None
        res[name] = top[1] if top else 0
        if top and top[1] >= 40:
            notify("flood-" + name, f"{name}: one error repeating {top[1]}x in 5 minutes", f"{top[0]}\n\nAll recent errors:\n" + "\n".join(f"{v:4d}  {k}" for k, v in sorted(recent.items(), key=lambda kv: -kv[1])[:8]) + f"\n\nFile: {f}", 2)
    return res


def rotate_logs():
    n = 0
    for g in LOG_GLOBS:
        for f in glob.glob(g):
            try:
                if os.path.getsize(f) > LOG_MAX:
                    with open(f, "rb") as fh:
                        fh.seek(-2 * 1024 * 1024, 2)
                        tail = fh.read()
                    with open(f + ".1", "wb") as o:
                        o.write(tail)
                    if not DRY:
                        os.truncate(f, 0)
                    n += 1
            except OSError:
                pass
    return n


def disk_check():
    free = shutil.disk_usage(HOME).free / 1e9
    if free < 10:
        notify("disk", f"Disk almost full: {free:.1f} GB free", "Agents write SQLite databases and logs; they will start failing when the disk fills. Clear space or ask Claude to trim data.", 6)
    return round(free, 1)


def backup():
    day = time.strftime("%Y-%m-%d")
    dest = os.path.join(BACKUP_ROOT, day)
    os.makedirs(dest, exist_ok=True)
    n = 0
    for d in DBS:
        for db in glob.glob(os.path.join(d, "*.db")):
            try:
                tgt = os.path.join(dest, os.path.basename(os.path.dirname(d)) + "__" + os.path.basename(os.path.dirname(db)) + "__" + os.path.basename(db))
                src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
                dst = sqlite3.connect(tgt)
                src.backup(dst)
                dst.close()
                src.close()
                n += 1
            except Exception as e:
                log(f"backup failed {db}: {e!r}")
    for d in DBS + [P("jev-client")]:                       # ledgers kept as JSON / JSONL (Predict, Options IV, Shadow, calls log): plain copies
        for f in glob.glob(os.path.join(d, "*.json*")):
            try:
                if os.path.getsize(f) < 60 * 1024 * 1024 and not os.path.basename(f).startswith(("heartbeat", "fomo_view")):
                    shutil.copy2(f, os.path.join(dest, os.path.basename(os.path.dirname(d)) + "__" + os.path.basename(os.path.dirname(f)) + "__" + os.path.basename(f)))
                    n += 1
            except OSError:
                pass
    for d in sorted(glob.glob(os.path.join(BACKUP_ROOT, "20*")))[:-7]:
        shutil.rmtree(d, ignore_errors=True)
    return n


def check_watchdog():
    """The 15-minute watchdog is the second layer: if it stopped, restart it and say so."""
    f = P("market-lab/ops/data/status.json")
    age = time.time() - os.path.getmtime(f) if os.path.exists(f) else None
    if age is None or age > 45 * 60:
        notify("watchdog-stale", "The 15-minute watchdog stopped running", f"ops/data/status.json is {'missing' if age is None else str(int(age // 60)) + ' min old'}. Kickstarted com.dhruv.watchdog.", 3)
        kick("com.dhruv.watchdog", "stale status.json")
        return "restarted"
    return "ok"


def tick(once=False):
    status = {"agents": check_agents(), "errors": check_errors(), "watchdog": check_watchdog()}
    now = time.time()
    if now - st["last_guard"] >= 60:                                  # stray-process guard (guard.py): kills `yes`-style CPU burners, alerts on runaways, duplicates, orphans and sustained load
        st["last_guard"] = now
        try:
            import guard
            status["guard"] = [f"{f['kind']}: {f['why']}" for f in guard.scan(act=not DRY, notify=notify, state=st["guard"])]
        except Exception as e:
            status["guard"] = [f"guard error {e!r}"]
    if now - st["last_disk"] > 600:
        st["last_disk"] = now
        status["disk_free_gb"] = disk_check()
    if now - st["last_rotate"] > 3600:
        st["last_rotate"] = now
        status["logs_rotated"] = rotate_logs()
    lt = time.localtime()
    if (lt.tm_hour, lt.tm_min) >= (2, 30) and lt.tm_hour < 6 and st["last_backup"] != time.strftime("%Y-%m-%d") and not DRY:
        st["last_backup"] = time.strftime("%Y-%m-%d")
        status["backup_dbs"] = backup()
        log(f"backup: {status['backup_dbs']} databases")
    bad = {k: v for k, v in status["agents"].items() if v not in ("ok", "stopped by user")}
    status["summary"] = "all agents ok" if not bad else f"{len(bad)} acted on: " + ", ".join(f"{k} {v}" for k, v in bad.items())
    save(status)
    return status


if __name__ == "__main__":
    if "--status" in sys.argv:
        s = json.load(open(STATE))
        print(f"{int(time.time() - s['t'])}s ago:", s["status"]["summary"])
        for k, v in s["status"]["agents"].items():
            print(f"  {k:32s} {v}")
    elif "--once" in sys.argv or DRY:
        print(json.dumps(tick(True), indent=1))
    else:
        log("supervisor started")
        while True:
            try:
                tick()
            except Exception as e:
                log(f"tick error {e!r}")
            time.sleep(30)
