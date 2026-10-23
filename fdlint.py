"""Static guard against unclosed sqlite connections (the 2026-10-23 dashboard leak). Scans every Jev repo's *.py with the AST:
  * `x = sqlite3.connect(...)` (or fdsafe.connect / _conn) where the function never calls x.close(), never uses `with closing(x)` / `with x`, and does not return/yield x  -> finding
  * a bare `sqlite3.connect(...).execute(...)` chain (nothing keeps the connection to close it)                                                          -> finding
Known findings are baselined in data/fdlint_baseline.json so only NEW ones alert. `python fdlint.py` lists all; `--baseline` accepts the current set; `--new` prints only new ones (exit 1 if any)."""
import ast, glob, json, os, sys

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "data", "fdlint_baseline.json")
ROOTS = [f"{HOME}/jev-*", f"{HOME}/market-lab", f"{HOME}/flip-notifier", f"{HOME}/zillow-agent", f"{HOME}/market-iv-agent", f"{HOME}/flux-lab"]
SKIP = ("/.venv/", "/node_modules/", "/__pycache__/", "/Archive/", "/.git/")
CONNECT = {"connect", "_conn", "_c"}


def _is_connect(call):
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr in CONNECT and isinstance(f.value, ast.Name) and f.value.id in ("sqlite3", "fdsafe"):
        return True
    return isinstance(f, ast.Name) and f.id in ("_conn",)


def lint_file(path):
    try:
        t = ast.parse(open(path, encoding="utf-8").read())
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []
    out = []
    for fn in [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        closed, withed, returned, assigned = set(), set(), set(), {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "close" and isinstance(n.func.value, ast.Name):
                closed.add(n.func.value.id)
            if isinstance(n, ast.With):
                for it in n.items:
                    e = it.context_expr
                    if isinstance(e, ast.Name):
                        withed.add(e.id)
                    if isinstance(e, ast.Call):
                        for a in e.args:
                            if isinstance(a, ast.Name):
                                withed.add(a.id)
                        if _is_connect(e):
                            withed.add("<with>")
            if isinstance(n, (ast.Return, ast.Yield)) and n.value is not None:
                for m in ast.walk(n.value):
                    if isinstance(m, ast.Name):
                        returned.add(m.id)
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and _is_connect(n.value):
                for tg in n.targets:
                    if isinstance(tg, ast.Name):
                        assigned[tg.id] = n.lineno
        for var, ln in assigned.items():
            if var not in closed and var not in withed and var not in returned and fn.name not in ("db", "_conn", "connect"):
                out.append((path, fn.name, ln, f"{var} = connect(...) is never closed"))
        for n in ast.walk(fn):                                           # chained: sqlite3.connect(...).execute(...)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Call) and _is_connect(n.value):
                if not any(isinstance(p, ast.With) for p in ast.walk(fn)):
                    out.append((path, fn.name, n.lineno, "connect(...).<call> chain: the connection can never be closed"))
    return out


def scan():
    res = []
    for root in ROOTS:
        for d in glob.glob(root):
            for dp, dn, fns in os.walk(d):
                if any(s in dp + "/" for s in SKIP):
                    dn[:] = []
                    continue
                for f in fns:
                    if f.endswith(".py"):
                        res += lint_file(os.path.join(dp, f))
    return res


def key(r):
    return f"{r[0].replace(HOME, '~')}::{r[1]}::{r[3]}"


if __name__ == "__main__":
    found = scan()
    if "--baseline" in sys.argv:
        json.dump(sorted({key(r) for r in found}), open(BASE, "w"), indent=0)
        print(f"baselined {len(found)} findings")
        sys.exit(0)
    known = set(json.load(open(BASE))) if os.path.exists(BASE) else set()
    show = [r for r in found if key(r) not in known] if "--new" in sys.argv else found
    for r in sorted(show):
        print(f"{r[0].replace(HOME, '~')}:{r[2]}  {r[1]}()  {r[3]}" + ("" if key(r) not in known else "   [baselined]"))
    print(f"{len(found)} findings, {len([r for r in found if key(r) not in known])} new")
    sys.exit(1 if [r for r in found if key(r) not in known] and "--new" in sys.argv else 0)
