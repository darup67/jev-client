"""Embed ui/jev-ui.css + ui/jev-ui.js into ~/jev-client/dashkit.js between /*JEVUI-BEGIN*/ and /*JEVUI-END*/ (idempotent).
dashkit.js is injected into EVERY Jev dashboard page by dashauth.with_kit (read fresh per request), so one build rolls the redesign out to all dashboards at their existing addresses.
Escape hatch (per browser and dashboard origin): ?classic=1 = old look, ?ui=new = back to the redesign. Pages that bring their own full theme set <meta name="jev-ui" content="custom">.
Roll back everything: python3 build_kit.py --off  (removes the block)."""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(os.path.dirname(HERE), "dashkit.js")
css = open(os.path.join(HERE, "jev-ui.css")).read()
js = open(os.path.join(HERE, "jev-ui.js")).read()
block = ("/*JEVUI-BEGIN*/\n(function(){try{\n var q=location.search,L=window.localStorage;\n if(/[?&]classic=1/.test(q))L.setItem('jevUi','classic');\n if(/[?&]ui=new/.test(q))L.removeItem('jevUi');\n if(L.getItem('jevUi')==='classic')return;\n"
         " var h=document.head;\n if(!document.getElementById('jev-fonts')){var f=document.createElement('link');f.id='jev-fonts';f.rel='stylesheet';f.href='https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap';h.appendChild(f);}\n"
         " var custom=document.querySelector('meta[name=\"jev-ui\"][content=\"custom\"]');\n if(!custom&&!document.getElementById('jev-ui-css')){var s=document.createElement('style');s.id='jev-ui-css';s.textContent=" + json.dumps(css) + ";h.appendChild(s);}\n"
         "}catch(e){}})();\n(function(){try{if(window.localStorage.getItem('jevUi')==='classic')return;}catch(e){}\n" + js + "\n})();\n/*JEVUI-END*/")
kit = open(KIT).read()
kit = re.sub(r"\n?/\*JEVUI-BEGIN\*/.*?/\*JEVUI-END\*/\n?", "\n", kit, flags=re.S)
if "--off" not in sys.argv:
    kit = kit.rstrip() + "\n" + block + "\n"
open(KIT, "w").write(kit)
print("dashkit.js:", "JEV UI removed" if "--off" in sys.argv else f"JEV UI embedded ({len(css)//1024} KB css, {len(js)//1024} KB js)", "| total", len(kit) // 1024, "KB")
