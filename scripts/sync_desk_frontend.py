"""Port the proven framework-independent desk controllers into Angular services.

The Angular host owns configuration, auth, lifecycle and route retention. Controllers
remain DOM/media adapters: call decisions continue to come from the brain. Run after
changing the original desk, and use --check in CI to prevent silent source drift.
"""

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "frontend/src/app/modules/desk"


def scoped_css(css):
    # Original root palette/font declarations are replaced by the Angular theme.
    # Body/state selectors must address the host, not a descendant body element.
    if "* {" in css:
        css = css[css.index("* {") :]
    css = re.sub(r"\bbody\b", "&", css)
    return re.sub(r"(?<![&\w])\[data-page=", "&[data-page=", css)


def outputs():
    result = {}
    logo = re.sub(
        r"{% comment %}[\s\S]*?{% endcomment %}",
        "",
        (ROOT / "templates/algodial/logo_symbol.html").read_text(),
    )
    for kind, app in (("agent", "agentdesk"), ("supervisor", "supervisor")):
        html = (ROOT / f"apps/{app}/templates/{app}/index.html").read_text()
        body = re.search(r"<body[^>]*>([\s\S]*)</body>", html)[1]
        body = re.sub(r'{% include "algodial/logo_symbol.html" %}', logo, body)
        body = body.replace('href="/admin/"', 'href="/"')
        if "{%" in body or "{{" in body:
            raise ValueError("A Django expression needs an explicit Angular port.")
        result[DEST / f"{kind}.component.html"] = (
            f'<div class="desk-runtime" data-page="{kind}" #runtime tabindex="-1">' + body + "</div>\n"
        )
        js = (ROOT / f"apps/{app}/static/{app}/app.js").read_text()
        start = js.index("(() => {")
        js = (
            js[:start]
            + f"export function mount{kind.title()}Desk(root, config) {{"
            + js[start + len("(() => {") :]
        )
        js = js.rsplit("})();", 1)[0] + "\n}\n"
        js = re.sub(r"\s*const config = JSON.parse\([^;]+;", "", js, count=1)
        prelude = """
  const abort = new AbortController(), timers = new Set();
  const setInterval = (fn,ms) => { const id=globalThis.setInterval(fn,ms);timers.add(id);return id; };
  const setTimeout = (fn,ms) => { const id=globalThis.setTimeout(()=>{timers.delete(id);fn();},ms);timers.add(id);return id; };
  const matchMedia = query => {
    const media=globalThis.matchMedia(query);
    return new Proxy(media,{get(target,key){
      if(key==='addEventListener')return (type,fn,options={})=>target.addEventListener(type,fn,{...options,signal:abort.signal});
      const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
    }});
  };
  const document = new Proxy(globalThis.document,{get(target,key){
    if(key==='body')return root;
    if(key==='getElementById')return id=>root.querySelector(`[id="${id}"]`);
    if(key==='querySelector'||key==='querySelectorAll')return root[key].bind(root);
    if(key==='addEventListener')return (type,fn,options={})=>target.addEventListener(type,event=>{
      if(root.isConnected && root.getClientRects().length)fn(event);
    },{...options,signal:abort.signal});
    const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
  }});
  const window = new Proxy(globalThis.window,{get(target,key){
    if(key==='addEventListener')return (type,fn,options={})=>target.addEventListener(type,fn,{...options,signal:abort.signal});
    const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
  }});
"""
        js = js.replace('  "use strict";', '  "use strict";\n' + prelude, 1)
        js = re.sub(r'let tokens = store.get\("[^"]+"\)', "let tokens = config.tokens()", js, count=1)
        # All token refreshes share Angular's single-flight refresh service.
        js = js.replace(
            "async function tokenRequest(fields) {",
            'async function tokenRequest(fields) {\n    if(fields.grant_type === "refresh_token") return config.refresh();',
        )
        js = js.replace(
            "function keepTokens(reply) {", "function keepTokens(reply) {\n    config.setTokens(reply);"
        )
        js = js.replace(
            "function showSignIn(message) {",
            "function showSignIn(message) {\n    if(abort.signal.aborted)return;\n    config.expired(message, tokens);",
        )
        for query in ("DESK_QUERY", "DESK"):
            statement = f"desk = await api({query});"
            js = js.replace(statement, statement + "\n    if(abort.signal.aborted)return;")
        js = js.replace(
            "if (!(await microphone())) return;",
            "if (!(await microphone()) || abort.signal.aborted) return;",
        )
        js = js.replace("JsSIP.debug.disable", "if(abort.signal.aborted)return;\n    JsSIP.debug.disable")
        # URL creation stays on this origin; the configured socket path is proxied to Django.
        if kind == "agent":
            dispose = "stopEverything();stopping=true;tokens=null;"
        else:
            dispose = "deskEpoch++;tokens=null;if(ua)ua.stop();if(live){live.onclose=null;live.close();}if(session)try{session.terminate();}catch{}"
        js = js.replace(
            "function onEvent(event) {",
            "function onEvent(event) {\n    if(abort.signal.aborted)return;\n    queueMicrotask(()=>config.changed?.());",
        )
        hook = f"""
  window.addEventListener('algodial:tokens',event=>{{tokens=event.detail;}});
  window.addEventListener('algodial:language',event=>{{root.querySelector(`[data-language="${{event.detail}}"]`)?.click();}});
  return {{dispose(){{{dispose}abort.abort();for(const id of timers){{clearTimeout(id);clearInterval(id);}}}},get active(){{return Boolean(session) || ['offered','talking','on_hold','wrapup'].includes(typeof state !== 'undefined' ? state?.activity : '');}}}};
"""
        js = js.replace("tokens.refresh !== refreshToken || ", "").replace("tokens !== previous || ", "")
        js = js.rsplit("\n}", 1)[0] + hook + "\n}\n"
        if kind == "supervisor":
            js = js.replace(
                "get active(){return Boolean(session) || ['offered','talking','on_hold','wrapup'].includes(typeof state !== 'undefined' ? state?.activity : '');}",
                "get active(){return false;}",
            )
        result[DEST / f"{kind}.controller.js"] = (
            "// Generated by scripts/sync_desk_frontend.py; keep transport behaviour in sync.\n" + js
        )
        css = (ROOT / f"apps/{app}/static/{app}/app.css").read_text()
        css = re.sub(
            r'url\(["\']?fonts/([^\)"\']+)["\']?\)',
            lambda m, app=app: f"url('/static/{app}/fonts/{m[1]}')",
            css,
        )
        result[DEST / f"{kind}.scss"] = (
            f"ad-{kind}{{display:block}}ad-{kind} .desk-runtime{{\n" + scoped_css(css) + "\n}\n"
        )
    shared = (ROOT / "static/algodial/ui.js").read_text()
    shared = shared[shared.index("  const paths = {") :].rsplit("})();", 1)[0]
    result[DEST / "desk-ui.js"] = "export function installDeskUi(){\n" + shared + "\n}\n"
    result[DEST / "supervisor-base.scss"] = (
        "ad-supervisor .desk-runtime{\n" + scoped_css((ROOT / "static/algodial/ui.css").read_text()) + "\n}\n"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, value in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"Run scripts/sync_desk_frontend.py: {path.relative_to(ROOT)} is stale")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)


if __name__ == "__main__":
    main()
