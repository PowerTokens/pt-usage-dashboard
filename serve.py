# -*- coding: utf-8 -*-
"""本地网页版控制台 —— 浏览器里粘贴 token → 拉数据 → 出看板。

具体场景：以前要看消耗得「装 Python → F12 复制 Cookie → 手改 config.json →
敲两条命令 → 自己找 HTML 双击」。现在只要跑一次 `python3 serve.py`，浏览器自动打开，
剩下的全在网页里点：粘贴 token、拉数据、看板直接渲染出来。

对应四个痛点：
  1. 上手太重   → 一条命令，自动开浏览器，之后不碰终端
  2. 取 token 烦 → 网页里粘贴，自动写进 config.json，不用 F12 改文件
  3. 没 token 看不了 → 内置示例数据「演示模式」，先看效果再说
  4. 过期报错看不懂 → 401 翻译成人话，页面里一键重新粘贴

安全：只监听 127.0.0.1（本机），token 只写进本机 config.json，不向任何第三方发送。
仅依赖 Python 3.8+ 标准库。
"""
import argparse
import datetime
import glob
import json
import os
import random
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import fetch_pt_server as fps  # noqa: E402
import gen_dashboard as gd  # noqa: E402

SKILL_DIR = HERE
CONFIG_PATH = os.path.join(SKILL_DIR, "config.json")
DEFAULT_SINCE = "2025-01-01"
DEFAULT_LANG = "en"  # 看板首次打开的语言；由 --lang 覆盖，页面右上角仍可随时切换

ACCENT = "#4f7cff"


# ---------------------------------------------------------------- 状态与缓存

def read_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def has_token():
    c = read_config()
    if c.get("session_token"):
        return True
    accts = c.get("accounts")
    if isinstance(accts, list) and any(a.get("session_token") for a in accts if isinstance(a, dict)):
        return True
    return bool(os.environ.get("PT_SESSION_TOKEN"))


def save_token(token):
    cfg = read_config()
    cfg["session_token"] = token.strip()
    cfg.setdefault("auth_scheme", "Bearer")
    cfg.setdefault("base_url", "https://www.powertokens.ai/api")
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def cache_status():
    """扫描 pt_usage_server*.json，返回每个缓存文件的概要。"""
    out = []
    for fp in sorted(glob.glob(os.path.join(SKILL_DIR, "pt_usage_server*.json"))):
        try:
            with open(fp, encoding="utf-8", errors="ignore") as fh:
                blob = json.load(fh) or {}
        except Exception:
            continue
        recs = blob.get("records") or []
        total = sum(r.get("amount") or 0 for r in recs)
        out.append({
            "file": os.path.basename(fp),
            "account": blob.get("account") or "default",
            "fetched_at": blob.get("fetched_at") or "-",
            "records": len(recs),
            "total": round(total, 4),
        })
    return out


# ---------------------------------------------------------------- 拉数据

def friendly_error(code, msg):
    m = str(msg or "")
    if code == 401 or "authorization" in m.lower() or "登录" in m:
        return ("登录已过期（PT 的会话 token 大约 3 天失效）。"
                "请重新打开 PT 网页登录，复制 Cookie `_Bearer` 的值，粘贴到下面重新拉取。")
    if code == -1 or "urlopen" in m or "timed out" in m or "Network" in m:
        return "连不上 PowerTokens（网络/代理问题）。检查网络后重试。"
    if code == 429:
        return "请求太频繁，PT 限流了。等一会儿再试。"
    return "拉取失败（code=%s）：%s" % (code, m[:200])


def do_fetch(since=DEFAULT_SINCE):
    """拉取全部账户并写缓存。返回 {ok, records, total, error, hint}。"""
    accounts = fps.load_accounts(SKILL_DIR, None)
    if not accounts:
        return {"ok": False, "error": "还没有 token，请先粘贴。", "hint": ""}

    now = int(time.time())
    since_ts = int(datetime.datetime.strptime(since, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())

    total_recs = 0
    total_amt = 0.0
    for (acc_name, acc_token) in accounts:
        api = fps.PTConsole(acc_token)
        # 先用余额接口探一次：401（过期/无效）和 -1（网络不通）都会在这里先暴露
        bal = api.post("/bill/balance", {})
        bcode = bal.get("code")
        if bcode == 401:
            return {"ok": False, "error": "token 无效或已过期",
                    "hint": friendly_error(401, bal.get("msg"))}
        if bcode == -1:
            return {"ok": False, "error": "连不上 PowerTokens",
                    "hint": friendly_error(-1, bal.get("msg"))}
        recs = fps.fetch_all_records(api, since_ts, now)

        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in acc_name)
        if acc_name == "default" or len(accounts) == 1:
            out_path = os.path.join(SKILL_DIR, "pt_usage_server.json")
        else:
            out_path = os.path.join(SKILL_DIR, "pt_usage_server_%s.json" % safe)

        usage = api.post("/dashboard/usage", {"start_timestamp": since_ts, "end_timestamp": now})
        rank = api.post("/dashboard/model-consume-rank", {"start_timestamp": since_ts, "end_timestamp": now})
        daily = api.post("/usage-records/daily-quota", {"start_timestamp": since_ts, "end_timestamp": now})

        blob = {
            "account": acc_name,
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "since": since,
            "range": {"start_timestamp": since_ts, "end_timestamp": now},
            "balance": bal.get("data") if bal.get("code") == 200 else None,
            "dashboard_usage": usage.get("data") if usage.get("code") == 200 else None,
            "model_rank": rank.get("data") if rank.get("code") == 200 else None,
            "daily_quota": (daily.get("data") or {}).get("buckets") if daily.get("code") == 200 else None,
            "records": recs,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, ensure_ascii=False)

        total_recs += len(recs)
        total_amt += sum(r.get("amount") or 0 for r in recs)

    if total_recs == 0:
        return {"ok": True, "records": 0, "total": 0.0,
                "warn": "拉到了 0 条记录。可能这个时间段确实没有调用，也可能 token 已失效（换个时间段试试或重新粘贴 token）。"}
    return {"ok": True, "records": total_recs, "total": round(total_amt, 4)}


# ---------------------------------------------------------------- 出看板

def build_dashboard(lang="en", theme="light"):
    """读缓存 → 聚合 → 渲染 HTML（与命令行版完全一致的管线）。"""
    server_meta = {}
    recs = []
    accounts_set = {}
    for fp in sorted(glob.glob(os.path.join(SKILL_DIR, "pt_usage_server*.json"))):
        acc = None
        try:
            with open(fp, encoding="utf-8", errors="ignore") as _fh:
                acc = (json.load(_fh) or {}).get("account")
        except Exception:
            acc = None
        if not acc:
            bn = os.path.basename(fp)
            if bn == "pt_usage_server.json":
                acc = "default"
            elif bn.startswith("pt_usage_server_") and bn.endswith(".json"):
                acc = bn[len("pt_usage_server_"):-len(".json")] or "default"
            else:
                acc = "default"
        r, meta = gd.load_server_usage(fp, acc)
        recs += r
        ac = accounts_set.setdefault(acc, {"calls": 0, "real": 0.0})
        ac["calls"] += len(r)
        ac["real"] += sum(x.get("cost_real") or 0 for x in r)
        if not server_meta and meta:
            server_meta = meta
    accounts_list = [{"name": n, "calls": v["calls"], "real": round(v["real"], 4)}
                     for n, v in accounts_set.items()]
    conv = gd.load_conversation_map(os.path.join(SKILL_DIR, "pt_conversation_map.json")) or {}
    catalog = gd.load_catalog(os.path.join(SKILL_DIR, "pt_models.json"))
    agg = gd.aggregate(recs)
    html = gd.render_html(SKILL_DIR, recs, agg, catalog, theme, server_meta,
                          conv, accounts=accounts_list, lang=lang)
    return html, len(recs)


def demo_records():
    """造一份样例数据（形状和真实缓存一致），走完全相同的解析管线。"""
    now = int(time.time())
    rnd = random.Random(20261012)
    models = [
        ("wan3.0-video", "video", 0.20, 5),
        ("seedance-2.5", "video", 0.35, 5),
        ("kling-v2", "video", 0.28, 5),
        ("deepseek-v4-pro", "chat", 0.0022, 0),
        ("glm-4.6", "chat", 0.0011, 0),
        ("kimi-k2", "chat", 0.0016, 0),
        ("flux-image", "image", 0.03, 0),
    ]
    recs = []
    for d in range(45):
        day_start = now - (44 - d) * 86400
        for _ in range(rnd.randint(4, 14)):
            model, mod, price, secs = rnd.choice(models)
            amount = round(price * (1 + rnd.random() * 0.4), 4)
            recs.append({
                "consume_time": day_start + rnd.randint(0, 86399),
                "model_id": model,
                "amount": amount,
                "quota": int(amount * 100),
                "usage": {"input_tokens": rnd.randint(100, 2500),
                          "output_tokens": rnd.randint(50, 1800)},
                "request_params": json.dumps({"seconds": secs, "resolution": "1080p"}) if secs else "{}",
                "status": "success",
                "request_type": mod,
                "consume_id": "demo%08d" % rnd.randint(0, 99999999),
            })
    recs.sort(key=lambda r: r["consume_time"])
    return {
        "account": "demo",
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "since": "demo",
        "range": {"start_timestamp": now - 45 * 86400, "end_timestamp": now},
        "balance": {"credits": 12345, "equal_usd": "12.34"},
        "dashboard_usage": None, "model_rank": None, "daily_quota": None,
        "records": recs,
    }


def build_demo(lang="en", theme="light"):
    """用示例数据渲染一份看板（不碰你的真实缓存）。"""
    tmpdir = tempfile.mkdtemp(prefix="pt_demo_")
    tmp = os.path.join(tmpdir, "demo.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(demo_records(), fh, ensure_ascii=False)
    recs, meta = gd.load_server_usage(tmp, "demo")
    agg = gd.aggregate(recs)
    html = gd.render_html(SKILL_DIR, recs, agg, None, theme, meta,
                          {}, accounts=[{"name": "demo", "calls": len(recs),
                                         "real": round(sum(r.get("cost_real") or 0 for r in recs), 4)}],
                          lang=lang)
    banner = (
        '<div style="max-width:1200px;margin:0 auto 14px;background:#fff7ed;'
        'border:1px solid #fed7aa;color:#9a3412;border-radius:12px;padding:12px 16px;'
        'font-size:13px;display:flex;gap:10px;align-items:center">'
        '<b>示例数据 · Demo data</b>'
        '<span style="color:#c2410c">下面是造出来的假数据，用来看效果，不是你的真实消耗。'
        '粘贴 token 拉取后看到的就是真实数据。</span></div>'
    )
    html = html.replace('<div class="wrap">', banner + '<div class="wrap">', 1)
    return html, len(recs)


# ---------------------------------------------------------------- 网页界面

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PowerTokens 用量看板 · 控制台</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
 background:#f4f6fb;color:#1f2937;padding:clamp(16px,4vw,40px);line-height:1.6}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}
.sub{color:#7b8494;font-size:13px;margin-bottom:22px}
.card{background:#fff;border:1px solid #e8ecf3;border-radius:16px;padding:20px;margin-bottom:16px;
 box-shadow:0 1px 3px rgba(16,24,40,.06),0 8px 22px -10px rgba(16,24,40,.12)}
.card h2{font-size:15px;margin:0 0 12px;font-weight:600}
label{display:block;font-size:13px;color:#374151;margin-bottom:6px;font-weight:500}
textarea{width:100%;min-height:90px;padding:10px 12px;border:1px solid #e8ecf3;border-radius:10px;
 font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;resize:vertical}
input[type=text]{width:100%;padding:9px 12px;border:1px solid #e8ecf3;border-radius:10px;font-size:13px}
button{background:#4f7cff;color:#fff;border:none;border-radius:10px;padding:10px 18px;font-size:14px;
 cursor:pointer;font-family:inherit;transition:.15s;font-weight:500}
button:hover{filter:brightness(1.08);transform:translateY(-1px)}
button.sec{background:#fff;color:#374151;border:1px solid #e8ecf3}
button.sec:hover{border-color:#4f7cff;color:#4f7cff}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.msg{margin-top:12px;font-size:13px;padding:10px 14px;border-radius:10px;display:none}
.msg.ok{background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0;display:block}
.msg.err{background:#fef2f2;color:#991b1b;border:1px solid #fecaca;display:block}
.msg.warn{background:#fffbeb;color:#92400e;border:1px solid #fde68a;display:block}
.msg.info{background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe;display:block}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef2f7}
th{color:#7b8494;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
.steps{font-size:13px;color:#4b5563;padding-left:20px;margin:8px 0}
.steps li{margin-bottom:4px}
code{background:#f1f5f9;padding:1px 6px;border-radius:5px;font-size:12px}
.pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:500}
.pill.ok{background:#dcfce7;color:#15803d}
.pill.no{background:#fee2e2;color:#b91c1c}
.note{font-size:12px;color:#7b8494;margin-top:14px}
</style>
</head>
<body>
<div class="wrap">
  <h1>PowerTokens 用量看板</h1>
  <div class="sub">在本机浏览器里粘贴 token，剩下的交给它。数据只在你这台电脑上跑。</div>

  <div class="card">
    <h2>1 · 会话 token</h2>
    <div id="tokenState"></div>
    <label for="tok">粘贴浏览器里 Cookie <code>_Bearer</code> 的值（一整串 JWT）</label>
    <textarea id="tok" placeholder="eyJhbGciOi...（整串粘贴进来）"></textarea>
    <div class="row">
      <button onclick="saveToken()">保存 token</button>
      <button class="sec" onclick="fetchData()">拉取数据</button>
    </div>
    <div id="msg" class="msg"></div>
    <details style="margin-top:14px">
      <summary style="cursor:pointer;font-size:13px;color:#4f7cff">这串 token 去哪儿找？</summary>
      <ol class="steps">
        <li>浏览器打开 <b>powertokens.ai</b> 并登录，进「用量 / 消耗」页</li>
        <li>按 <code>F12</code> → <b>Application（应用）</b> → <b>Cookies</b> → 站点 <code>https://www.powertokens.ai</code></li>
        <li>找到名为 <code>_Bearer</code> 的那条，复制它的 <b>Value</b></li>
        <li>粘回上面的框里，点「保存 token」</li>
      </ol>
      <div class="note">它随登录会话失效，实测约 3 天。过期后页面会明说，重新粘一次就行 —— 这是 PT 网页的鉴权机制，绕不开。</div>
    </details>
  </div>

  <div class="card">
    <h2>2 · 看板</h2>
    <div id="cacheState"></div>
    <div class="row">
      <button onclick="openDash('en')">打开看板（English）</button>
      <button class="sec" onclick="openDash('zh')">打开看板（中文）</button>
      <button class="sec" onclick="openDemo()">先看示例看板（无需 token）</button>
    </div>
  </div>

  <div class="card">
    <h2>已缓存的数据</h2>
    <div id="cacheTable"></div>
  </div>
</div>
<script>
function show(cls, text){
  const m=document.getElementById('msg');
  m.className='msg '+cls; m.innerHTML=text;
}
async function jfetch(url, opts){
  const r=await fetch(url, opts); return await r.json();
}
async function refresh(){
  const s=await jfetch('/api/status');
  document.getElementById('tokenState').innerHTML = s.has_token
    ? '<span class="pill ok">token 已保存</span>'
    : '<span class="pill no">还没有 token</span> <span class="sub" style="margin:0">没保存也能先看示例看板</span>';
  let ct='<div class="sub">还没有缓存数据。保存 token 后点「拉取数据」。</div>';
  if(s.cache && s.cache.length){
    ct='<table><thead><tr><th>账户</th><th>拉取时间</th><th>记录数</th><th>金额</th></tr></thead><tbody>'+
      s.cache.map(c=>'<tr><td>'+c.account+'</td><td>'+c.fetched_at+'</td><td>'+c.records+'</td><td>$'+c.total.toFixed(4)+'</td></tr>').join('')+
      '</tbody></table>';
  }
  document.getElementById('cacheTable').innerHTML=ct;
  document.getElementById('cacheState').innerHTML = s.cache && s.cache.length
    ? '<div class="sub" style="margin:0 0 4px">已缓存 '+s.cache.reduce((a,b)=>a+b.records,0)+' 条记录，直接打开就是上次拉的结果。</div>'
    : '';
}
async function saveToken(){
  const t=document.getElementById('tok').value.trim();
  if(!t){ show('err','先把 token 粘进去。'); return; }
  show('info','保存中…');
  const r=await jfetch('/api/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})});
  if(r.ok){ show('ok','token 已保存到本机 config.json。现在点「拉取数据」。'); refresh(); }
  else show('err','保存失败：'+(r.error||'未知错误'));
}
async function fetchData(){
  show('info','正在从 PowerTokens 拉取，可能要十几秒到一分钟…');
  const r=await jfetch('/api/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
  if(r.ok){
    show('ok','拉到 '+r.records+' 条记录，合计 $'+Number(r.total||0).toFixed(4)+'。点下面「打开看板」查看。'+(r.warn?'<br>'+r.warn:''));
    refresh();
  } else {
    show('err',(r.error||'拉取失败')+(r.hint?'<br><br>'+r.hint:''));
    refresh();
  }
}
function openDash(lang){ window.open('/dashboard?lang='+lang,'_blank'); }
function openDemo(){ window.open('/demo','_blank'); }
refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[serve] " + (fmt % args) + "\n")

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", code)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        lang = (q.get("lang", [DEFAULT_LANG])[0] or DEFAULT_LANG).lower()
        if lang not in ("en", "zh"):
            lang = DEFAULT_LANG
        try:
            if u.path in ("/", "/index.html"):
                self._send(PAGE)
            elif u.path == "/api/status":
                self._json({"has_token": has_token(), "cache": cache_status()})
            elif u.path == "/dashboard":
                html, n = build_dashboard(lang=lang)
                self._send(html)
                print("[serve] 看板已渲染：%d 条记录" % n)
            elif u.path == "/demo":
                html, n = build_demo(lang=lang)
                self._send(html)
                print("[serve] 示例看板已渲染：%d 条" % n)
            else:
                self._send("Not found", "text/plain; charset=utf-8", 404)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send("出错了：%s" % e, "text/plain; charset=utf-8", 500)

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        try:
            if u.path == "/api/token":
                tok = (payload.get("token") or "").strip()
                if not tok:
                    self._json({"ok": False, "error": "token 为空"})
                    return
                save_token(tok)
                self._json({"ok": True})
            elif u.path == "/api/fetch":
                since = payload.get("since") or DEFAULT_SINCE
                self._json(do_fetch(since))
            else:
                self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json({"ok": False, "error": "服务端异常：%s" % e}, 500)


def main():
    ap = argparse.ArgumentParser(description="PT 用量看板 · 本地网页控制台")
    ap.add_argument("--port", type=int, default=8777, help="监听端口（默认 8777）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--lang", default="en", choices=["en", "zh"],
                    help="看板首次打开的语言（默认 en；页面右上角仍可随时切换）")
    args = ap.parse_args()

    global DEFAULT_LANG
    DEFAULT_LANG = args.lang

    url = "http://%s:%d/" % (args.host, args.port)
    print("=" * 56)
    print(" PT 用量看板 · 本地控制台")
    print(" 地址：%s" % url)
    print(" 语言：%s（页面右上角可切换）" % args.lang)
    print(" 只监听本机，token 不出这台电脑。按 Ctrl+C 停止。")
    print("=" * 56)

    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
