"""File-descriptor guard (2026-10-23). Why: the Coin Desk dashboard leaked ~4 sqlite fds per page load, hit launchd's default 256-file limit, and then could not even read its own
page ("OSError 24 Too many open files"): the phone could not open it and nothing alerted. This guard makes that class of failure loud and self-healing for EVERY job in the supervisor REGISTRY.
Every minute (called from supervisor.tick): count each running job's open files and compare with its real soft limit (launchctl print; 256 when none is set).
  >= 60 %  -> alert (email, deduped 3 h)
  >= 85 %  -> restart the job (launchd KeepAlive brings it back with a clean slate) + alert. NEVER for PROTECTED jobs (alert only).
  leak trend: >= 10 samples (10 min) that only rise by >= 40 fds and sit above 120 -> "leak suspected" alert, long before the limit.
`python fdguard.py` prints the table.  `python fdguard.py harden` raises the limit in every Jev plist (backup *.plist.fdbak; no reload: takes effect at the job's next restart)."""
import json, os, plistlib, re, subprocess, sys, time

HOME = os.path.expanduser("~")
UID = os.getuid()
PROTECTED = {"com.dhruv.jevmajors.main"}        # the user's rule: never stop the hidden signal engine; alert only
WARN, KILL, TREND_RISE, TREND_MIN = 0.60, 0.85, 40, 120
SOFT, HARD = 4096, 8192
_LIM = {}


def fd_count(pid):
    r = subprocess.run(["lsof", "-n", "-P", "-p", str(pid)], capture_output=True, text=True, timeout=20)
    return max(0, len(r.stdout.splitlines()) - 1)


def soft_limit(label):
    """Soft maxfiles of the loaded job (launchctl print), cached 10 min; 256 = launchd's default when the plist sets none."""
    hit = _LIM.get(label)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    lim = 256
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{UID}/{label}"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"maxfiles \(soft\) => (\d+)", out)
        if m:
            lim = int(m.group(1))
    except Exception:
        pass
    _LIM[label] = (time.time(), lim)
    return lim


def scan(registry, job, kick, notify, act=True, state=None):
    """registry = supervisor REGISTRY; job(label) -> {'pid'}; kick(label, why); notify(key, subject, body, every_h). Returns {label: 'n/limit ok|WARN|RESTARTED'}."""
    state = state if state is not None else {}
    out = {}
    for row in registry:
        label = row[0]
        try:
            pid = job(label).get("pid")
            if not pid:
                continue
            n, lim = fd_count(pid), soft_limit(label)
            pct = n / lim
            h = state.setdefault(label, {"pid": pid, "hist": []})
            if h["pid"] != pid:                                        # restarted: new history
                h.update(pid=pid, hist=[])
            h["hist"] = (h["hist"] + [n])[-10:]
            name = label.replace("com.dhruv.", "")
            verdict = "ok"
            hist = h["hist"]
            rising = len(hist) >= 10 and all(b >= a for a, b in zip(hist, hist[1:])) and hist[-1] - hist[0] >= TREND_RISE and hist[-1] >= TREND_MIN
            if pct >= KILL:
                verdict = "RESTART" if label not in PROTECTED else "ALERT (protected)"
                notify("fd-kill-" + label, f"{name}: {n}/{lim} open files, {'restarting it' if label not in PROTECTED else 'protected job, not restarting'}",
                       f"{label} has {n} of {lim} open files ({pct:.0%}). Past the limit it fails every request (OSError 24). This is the Coin Desk dashboard leak of 2026-10-23: a connection or file opened per request and never closed. Find it with `lsof -p {pid}` and fix the code; fdlint.py lists suspects.", 1)
                if act and label not in PROTECTED:
                    if lim < SOFT:
                        reload(label)                                   # kickstart keeps the OLD limits; bootout+bootstrap re-reads the (hardened) plist
                    else:
                        kick(label, f"open files {n}/{lim}")
                    verdict = "RESTARTED"
            elif pct >= WARN:
                verdict = "WARN"
                notify("fd-warn-" + label, f"{name}: {n}/{lim} open files ({pct:.0%})", f"{label} is using {pct:.0%} of its file limit. It restarts itself at {KILL:.0%}. Run `python3 ~/jev-client/fdguard.py` and `lsof -p {pid}` to see what is open.", 3)
            elif rising:
                verdict = "LEAK?"
                notify("fd-leak-" + label, f"{name}: open files only rising ({hist[0]} -> {hist[-1]} in 10 min)", f"{label} open files never fell in 10 minutes ({hist}). That is the signature of a leak; limit {lim}. Check for unclosed sqlite connections / sockets in its handlers.", 6)
            out[name] = f"{n}/{lim} {verdict}"
        except Exception as e:
            out[label.replace("com.dhruv.", "")] = f"error {e!r}"[:60]
    return out


def reload(label):
    """Re-read the plist: bootout then bootstrap (a plain kickstart would keep the old resource limits)."""
    p = os.path.join(HOME, "Library", "LaunchAgents", label + ".plist")
    subprocess.run(["launchctl", "bootout", f"gui/{UID}/{label}"], capture_output=True)
    time.sleep(2)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", p], capture_output=True, text=True)
    _LIM.pop(label, None)
    return r.returncode == 0


def harden(labels=None):
    """Add SoftResourceLimits/HardResourceLimits NumberOfFiles to every com.dhruv.* plist that lacks them (backup beside it). No reload."""
    done = []
    d = os.path.join(HOME, "Library", "LaunchAgents")
    for f in sorted(os.listdir(d)):
        if not (f.startswith("com.dhruv.") and f.endswith(".plist")):
            continue
        p = os.path.join(d, f)
        pl = plistlib.load(open(p, "rb"))
        if (pl.get("SoftResourceLimits") or {}).get("NumberOfFiles", 0) >= SOFT:
            continue
        open(p + ".fdbak", "wb").write(open(p, "rb").read())
        pl["SoftResourceLimits"] = {**(pl.get("SoftResourceLimits") or {}), "NumberOfFiles": SOFT}
        pl["HardResourceLimits"] = {**(pl.get("HardResourceLimits") or {}), "NumberOfFiles": HARD}
        plistlib.dump(pl, open(p, "wb"))
        done.append(f)
    return done


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if "harden" in sys.argv:
        r = harden()
        print(f"raised limit to {SOFT} in {len(r)} plists (effective at each job's next restart):")
        print("\n".join("  " + x for x in r))
    else:
        import supervisor
        res = scan(supervisor.REGISTRY, supervisor.job, lambda *a: None, lambda *a, **k: None, act=False)
        for k, v in sorted(res.items()):
            print(f"{k:40s} {v}")
