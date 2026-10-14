"""Build an offline gallery from the production UI browser-test screenshots."""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "test-artifacts"
PATTERN = re.compile(r"(.+)-(light|dark)-(1366x768|1920x1080)\.png$")


def build():
    cards = []
    for workspace in ("agentdesk", "supervisor", "management"):
        for path in sorted((ROOT / workspace).glob("*.png")):
            match = PATTERN.fullmatch(path.name)
            if not match:
                continue
            state, theme, size = match.groups()
            label = html.escape(state.replace("-", " ").title())
            address = html.escape(path.relative_to(ROOT).as_posix(), quote=True)
            full = path.with_stem(path.stem + "-full")
            extra = (
                f'<a href="{html.escape(full.relative_to(ROOT).as_posix(), quote=True)}">Entire form</a>'
                if full.exists()
                else ""
            )
            cards.append(
                f'<article data-workspace="{workspace}" data-theme="{theme}" data-size="{size}">'
                f"<h2>{label}</h2><p>{workspace} / {theme} / {size}</p>"
                f'<a href="{address}"><img loading="lazy" src="{address}" alt="{label}, {theme}, {size}"></a>'
                f'<p><a href="{address}">Original PNG</a> {extra}</p></article>'
            )
    document = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AlgoDial UI review</title>
<style>
:root{color-scheme:light dark}body{font:16px/1.5 system-ui,sans-serif;max-width:1600px;margin:32px auto;padding:0 24px}
h1{font-size:28px}h2{font-size:18px}a{color:light-dark(#2e6e73,#7cc3c8)}
.filters{display:flex;gap:24px;flex-wrap:wrap;padding:16px 0;border-block:1px solid #7a8688;margin:24px 0}
label{display:flex;align-items:center;gap:8px}select{padding:8px;font:inherit;min-height:40px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:32px}
article{min-width:0;border-bottom:1px solid #7a8688;padding-bottom:16px}article p{font-size:14px}
img{display:block;width:100%;height:auto;border:1px solid #7a8688}article p a{margin-right:24px}
[hidden]{display:none!important}:focus-visible{outline:3px solid #2e6e73;outline-offset:3px}
</style></head><body><h1>AlgoDial production UI</h1>
<p>Demo-centre screenshots of the implemented pages. Open an original PNG for readable controls, or the entire form for long management pages.</p>
<div class="filters"><label>Workspace <select id="workspace"><option value="">All</option><option value="agentdesk">Agent</option><option value="supervisor">Supervisor</option><option value="management">Management</option></select></label>
<label>Theme <select id="theme"><option value="light">Light</option><option value="dark">Dark</option></select></label>
<label>Viewport <select id="size"><option>1366x768</option><option>1920x1080</option></select></label></div>
<p id="count" role="status"></p><main>"""
        + "\n".join(cards)
        + """</main><script>
function filter(){let count=0;for(const card of document.querySelectorAll('article')){card.hidden=['workspace','theme','size'].some(key=>document.getElementById(key).value && card.dataset[key]!==document.getElementById(key).value);if(!card.hidden)count++;}document.getElementById('count').textContent=`${count} screenshots`;}
document.querySelectorAll('select').forEach(input=>input.addEventListener('change',filter));filter();
</script></body></html>"""
    )
    ROOT.mkdir(exist_ok=True)
    (ROOT / "index.html").write_text(document)
    print(f"Gallery: {ROOT / 'index.html'} ({len(cards)} screenshots)")


if __name__ == "__main__":
    build()
