"""jevui.inject(html_bytes) -> html_bytes with ONLY the Jev UI block (generic theme + dashboard switcher) from dashkit.js, for the dashboards that do not use the full dashkit overlay
(HQ, Shop, Agents, Career, Home). Dashboards that call dashauth.with_kit already get the same block through dashkit.js. Safe: returns the page unchanged on any problem."""
import os, re


def inject(body):
    try:
        kit = open(os.path.expanduser("~/jev-client/dashkit.js")).read()
        m = re.search(r"/\*JEVUI-BEGIN\*/.*?/\*JEVUI-END\*/", kit, re.S)
        if not m:
            return body
        tag = "<script>" + m.group(0).replace("</script>", "<\\/script>") + "</script>"
        raw = body.decode("utf-8") if isinstance(body, bytes) else body
        out = raw.replace("</body>", tag + "</body>", 1) if "</body>" in raw else raw + tag
        return out.encode("utf-8") if isinstance(body, bytes) else out
    except Exception:
        return body
