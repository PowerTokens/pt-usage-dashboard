# -*- coding: utf-8 -*-
"""PowerTokens 用量看板生成器（单文件离线 HTML，零外部依赖）

合并三类数据源，统计 PT 模型调用的成本/次数，渲染成离线 HTML：
  1) 【权威】PT 控制台真实消耗  pt_usage_server.json（fetch_pt_server.py 拉取）
     —— 逐笔精确 amount / quota / usage token，并能拿到账户余额与官方聚合。
        返回 cost_real，是唯一「不靠估算」的数据源。
  2) 本地记录器                 <skill>/pt_calls.jsonl       （pt_log.py 写入）
  3) WorkBuddy 日志             ~/.workbuddy/projects/**/*.jsonl（providerData.model 命中 PT 特征）

可选：<skill>/pt_models.json（fetch_catalog.py 拉取）作为模型目录对照表并入。

仅依赖 Python 标准库（3.8+）。离线可看，无 CDN。
"""
import argparse, json, glob, os, sys, datetime, re

DISPLAY_TZ_OFFSET = 8 * 3600   # 看板按北京时间展示（PT 返回的是 UTC 时间戳）

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ----------------------------------------------------------------------------
# 配置：价格表（USD）。基价 = 5s / 720P 档；时长线性、分辨率倍数见 estimate_cost。
# 数据来自 powertokens-integration skill 实测（5s/720p 真实预扣），其余按系数推算，仅为估算。
# 不同套餐/时段可能不同，用户可自行修改本表。
# ----------------------------------------------------------------------------
PRICE = {
    # 视频（基价 @5s/720p）
    "wan3.0-video": 0.20,
    "wan3.0-video-prime": 0.70,
    "wan2.7-t2v": 0.475,
    "seedance-1-5-pro-251215": 0.247,
    "dreamina-seedance-2-0-fast-260128": 0.605,
    "dreamina-seedance-2-0-260128": 0.756,
    "kling-video-o1": 0.42,
    "kling-v3": 0.42,
    "kling-v2-1-master": 1.40,
    "hunyuan-video": 0.30,
    # 图像（按张，基价）
    "flux": 0.04,
    "kolors": 0.03,
    "cogview": 0.03,
    "seedream": 0.05,
    # 音频（按次/按秒，基价）
    "cosyvoice": 0.01,
    "seed-tts": 0.01,
    "music": 0.05,
    # 对话（按百万 token，基价输入；这里仅作占位，媒体场景一般不用）
    "deepseek-v4-pro": 0.0,
}
RES_FACTOR = {"480P": 0.5, "720P": 1.0, "1080P": 2.0, "default": 1.0}

# PT 模型特征（用于 source A 识别）。
# 只保留「PT 专属的媒体/生成品牌」，不要放通用 LLM 家族名（deepseek/glm/qwen/minimax…），
# 否则会误吞 WorkBuddy 里大量普通 LLM 调用（那些 provider 通常为空、并非走 PT）。
# 用户若把某 LLM 经 PT 网关调用，会在 provider 字段留下 powertokens/pt，由下方 provider 命中。
PT_MODEL_HINTS = [
    "seedance", "wan", "kling", "dreamina", "hunyuan-video",
    "flux", "kolors", "cogview", "seedream", "cosyvoice", "seed-tts",
]
PT_PROVIDERS = {"powertokens", "pt", "powertokens.ai"}

MODALITY_KEYWORDS = {
    "video": ["seedance", "wan", "kling", "dreamina", "hunyuan-video", "minimax", "video", "step"],
    "image": ["flux", "kolors", "cogview", "seedream", "image", "sd3", "stable-diffusion"],
    "audio": ["cosyvoice", "seed-tts", "music", "audio", "tts", "speech"],
    "chat":  ["deepseek", "glm", "qwen", "doubao", "gpt", "claude", "chat", "abab", "moonshot"],
}


def _local_dt(ts):
    """PT 返回 UTC 时间戳，按北京时间(+8)换算后展示。"""
    return datetime.datetime.fromtimestamp(ts + DISPLAY_TZ_OFFSET, datetime.UTC)


def day_str(ts):
    return _local_dt(ts).strftime("%Y-%m-%d") if ts else "unknown"


def time_str(ts):
    return _local_dt(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def classify_modality(model):
    m = (model or "").lower()
    for mod, kws in MODALITY_KEYWORDS.items():
        if any(k in m for k in kws):
            return mod
    return "other"


def is_pt_model(model, provider=None):
    m = (model or "").lower()
    if provider and provider.lower() in PT_PROVIDERS:
        return True
    return any(h in m for h in PT_MODEL_HINTS)


def estimate_cost(model, seconds, resolution):
    base = PRICE.get(model)
    if base is None:
        # 模糊匹配前缀（如 seedance-1-5-pro-251215 -> seedance 命中则退回最近基价）
        for k, v in PRICE.items():
            if model and model.startswith(k.split("-")[0]) and v > 0:
                base = v
                break
    if base is None or base == 0:
        return None
    secs = float(seconds) if seconds else 5.0
    res = (resolution or "720P").upper()
    factor = RES_FACTOR.get(res, RES_FACTOR["default"])
    # 视频/图像按时长×分辨率；音频/对话暂按基价
    mod = classify_modality(model)
    if mod in ("video", "image"):
        return round(base * (secs / 5.0) * factor, 4)
    return round(base, 4)


def parse_ts(v):
    if isinstance(v, (int, float)):
        if v > 1e12:
            return v / 1000.0
        if v > 1e9:
            return float(v)
    if isinstance(v, str):
        s = v.strip()
        for fmt_ in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
                    '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                import calendar
                return calendar.timegm(datetime.datetime.strptime(s, fmt_).timetuple())
            except Exception:
                pass
        try:
            return float(s) / 1000.0
        except Exception:
            return None
    return None


# ----------------------------------------------------------------------------
# 数据源加载
# ----------------------------------------------------------------------------
def load_logger_calls(path):
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict):
                continue
            recs.append({
                "ts": o.get("ts"),
                "model": o.get("model", "unknown"),
                "modality": o.get("modality") or classify_modality(o.get("model")),
                "seconds": o.get("seconds"),
                "resolution": o.get("resolution"),
                "status": o.get("status", "unknown"),
                "cost_est": o.get("cost_est"),
                "cost_real": o.get("cost_real"),
                "note": o.get("note", ""),
                "source": "logger",
            })
    return recs


def load_server_usage(path, account="default"):
    """读取 fetch_pt_server.py 拉到的 PT 控制台真实消耗（Source C，权威）。

    字段见 /usage-records/list：consume_time / model_id / amount / quota / usage / request_params …
    amount 是 PT 服务端给出的精确扣费，放 cost_real。
    account 用于多账户合并时给每条记录打标（与文件名/文件内 account 字段对应）。
    """
    recs = []
    meta = {}
    if not os.path.exists(path):
        return recs, meta
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            blob = json.load(fh)
    except Exception:
        return recs, meta

    meta = {
        "fetched_at": blob.get("fetched_at"),
        "balance": blob.get("balance"),
        "dashboard": blob.get("dashboard_usage"),
        "rank": blob.get("model_rank"),
        "daily_quota": blob.get("daily_quota"),
        "account": blob.get("account") or account,
    }
    for it in (blob.get("records") or []):
        ts = it.get("consume_time")
        model = it.get("model_id") or it.get("model_name") or "unknown"
        rtype = (it.get("request_type") or "").lower()
        if rtype in ("text", "chat", "llm"):
            mod = "chat"
        elif "video" in rtype or any(k in model.lower() for k in ("seedance", "kling", "wan", "hailuo", "dreamina")):
            mod = "video"
        elif "image" in rtype:
            mod = "image"
        elif "audio" in rtype or "tts" in rtype:
            mod = "audio"
        else:
            mod = classify_modality(model)
        # 尝试从 request_params 里捡出时长 / 分辨率
        secs, res = None, None
        try:
            rp = json.loads(it.get("request_params") or "{}")
            if isinstance(rp, dict):
                for k in ("seconds", "duration", "video_duration"):
                    if isinstance(rp.get(k), (int, float)):
                        secs = rp[k]
                        break
                for k in ("resolution", "size", "aspect_ratio"):
                    if isinstance(rp.get(k), str):
                        res = rp[k]
                        break
        except Exception:
            pass
        us = it.get("usage") or {}
        tin = us.get("input_tokens") or us.get("prompt_tokens") or 0
        tout = us.get("output_tokens") or us.get("completion_tokens") or 0
        amount = it.get("amount")
        recs.append({
            "ts": ts,
            "model": model,
            "modality": mod,
            "seconds": secs,
            "resolution": res,
            "tin": tin if isinstance(tin, (int, float)) else 0,
            "tout": tout if isinstance(tout, (int, float)) else 0,
            "status": it.get("status", "unknown"),
            "cost_est": None,                       # 服务端已给精确值，不再估算
            "cost_real": float(amount) if isinstance(amount, (int, float)) else None,
            "note": (it.get("consume_id") or "")[:24],
            "source": "server",
            "account": account,
        })
    return recs, meta


def load_conversation_map(path):
    """读取 join_conversations.py 的产出：按 API key 拆分 + 调用↔对话归因。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_workbuddy_logs(projects_dir):
    recs = []
    if not os.path.isdir(projects_dir):
        return recs
    files = glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True)
    for fp in files:
        try:
            with open(fp, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(o, dict):
                        continue
                    pd = o.get("providerData") if isinstance(o.get("providerData"), dict) else {}
                    model = pd.get("model") or pd.get("requestModelId") or pd.get("requestModelName") or o.get("model")
                    provider = (pd.get("provider") or o.get("provider") or "")
                    if not model or not is_pt_model(model, provider):
                        continue
                    ts = parse_ts(pd.get("timestamp") or o.get("timestamp") or o.get("createdAt") or o.get("time"))
                    cost = pd.get("cost") or pd.get("price") or o.get("cost")
                    cost = float(cost) if isinstance(cost, (int, float)) else None
                    recs.append({
                        "ts": ts,
                        "model": model,
                        "modality": classify_modality(model),
                        "seconds": pd.get("seconds"),
                        "resolution": pd.get("resolution"),
                        "status": "success" if cost is None else "unknown",
                        "cost_est": cost,   # WorkBuddy 日志若带 cost 直接用，否则未知
                        "cost_real": None,
                        "note": "from WorkBuddy log",
                        "source": "workbuddy",
                    })
        except Exception:
            continue
    return recs


def load_catalog(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return json.load(fh)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 聚合
# ----------------------------------------------------------------------------
def aggregate(recs):
    by_model = {}
    by_mod = {}
    by_day = {}
    total_est = 0.0
    total_real = 0.0
    est_n = 0
    real_n = 0
    for r in recs:
        m = r["model"]
        mod = r["modality"]
        day = day_str(r["ts"])
        bm = by_model.setdefault(m, {"calls": 0, "est": 0.0, "real": 0.0, "mod": mod})
        bm["calls"] += 1
        md = by_mod.setdefault(mod, {"calls": 0, "est": 0.0, "real": 0.0})
        md["calls"] += 1
        dy = by_day.setdefault(day, {"est": 0.0, "real": 0.0, "calls": 0})
        dy["calls"] += 1
        if r["cost_est"] is not None:
            bm["est"] += float(r["cost_est"]); md["est"] += float(r["cost_est"]); dy["est"] += float(r["cost_est"]); total_est += float(r["cost_est"]); est_n += 1
        if r["cost_real"] is not None:
            bm["real"] += float(r["cost_real"]); md["real"] += float(r["cost_real"]); dy["real"] += float(r["cost_real"]); total_real += float(r["cost_real"]); real_n += 1
    return {
        "by_model": by_model, "by_mod": by_mod, "by_day": by_day,
        "total_est": total_est, "total_real": total_real, "est_n": est_n, "real_n": real_n,
    }


# ----------------------------------------------------------------------------
# HTML 渲染（内联 JSON + 原生 JS 画 SVG，无 CDN）
# ----------------------------------------------------------------------------
def render_html(skill_dir, recs, agg, catalog, theme, server_meta=None, conv=None, accounts=None, lang="en"):
    server_meta = server_meta or {}
    conv = conv or {}
    recs_sorted = sorted([r for r in recs if r["ts"]], key=lambda x: x["ts"])
    recs_sorted = recs_sorted + [r for r in recs if not r["ts"]]
    recent = list(reversed(recs_sorted))[:200]

    # 日序列（按天聚合，补零）
    days = sorted(agg["by_day"].keys())
    daily = []
    for d in days:
        v = agg["by_day"][d]
        daily.append({"day": d, "est": round(v["est"], 4), "real": round(v["real"], 4), "calls": v["calls"]})

    model_rows = sorted(agg["by_model"].items(), key=lambda kv: (kv[1]["est"] + kv[1]["real"]), reverse=True)
    mod_rows = sorted(agg["by_mod"].items(), key=lambda kv: (kv[1]["est"] + kv[1]["real"]), reverse=True)

    # 逐笔紧凑数组，供前端做时间筛选 + 分布图：[ts, model, tin, tout, real, est, src]
    src_idx = {"server": 0, "logger": 1, "workbuddy": 2}
    recs_compact = []
    for r in recs:
        if not r["ts"]:
            continue
        recs_compact.append([
            int(r["ts"]), r["model"],
            int(r.get("tin") or 0), int(r.get("tout") or 0),
            round(float(r["cost_real"]), 6) if r.get("cost_real") is not None else 0,
            round(float(r["cost_est"]), 6) if r.get("cost_est") is not None else 0,
            src_idx.get(r["source"], 0),
            r.get("account", ""),
        ])

    data = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kpi": {
            "calls": len(recs),
            "est": round(agg["total_est"], 4),
            "real": round(agg["total_real"], 4),
            "est_n": agg["est_n"],
            "real_n": agg["real_n"],
            "top_model": model_rows[0][0] if model_rows else "-",
            "wb": sum(1 for r in recs if r["source"] == "workbuddy"),
            "log": sum(1 for r in recs if r["source"] == "logger"),
        },
        "daily": daily,
        "models": [{"model": k, "calls": v["calls"], "est": round(v["est"], 4), "real": round(v["real"], 4), "mod": v["mod"]} for k, v in model_rows],
        "mods": [{"mod": k, "calls": v["calls"], "est": round(v["est"], 4), "real": round(v["real"], 4)} for k, v in mod_rows],
        "recent": [
            {
                "ts": time_str(r["ts"]),
                "model": r["model"], "mod": r["modality"], "sec": r["seconds"], "res": r["resolution"],
                "status": r["status"], "est": r["cost_est"], "real": r["cost_real"], "src": r["source"], "note": r.get("note", "")
            } for r in recent
        ],
        "catalog": (catalog.get("models") if isinstance(catalog, dict) else catalog) if catalog else None,
        "price_keys": list(PRICE.keys()),
        "server": {
            "fetched_at": server_meta.get("fetched_at"),
            "balance": server_meta.get("balance"),
            "dashboard": server_meta.get("dashboard"),
            "rank": server_meta.get("rank"),
        },
        "recs": recs_compact,
        "accounts": accounts or [],
        "server_n": sum(1 for r in recs if r["source"] == "server"),
        "conv": {
            "stat": conv.get("stat"),
            "totals": conv.get("totals"),
            "keys": conv.get("keys") or [],
            "sessions": conv.get("sessions") or [],
            "cwd": conv.get("cwd") or [],
            "generated": conv.get("generated"),
        },
    }
    data_json = json.dumps(data, ensure_ascii=False)

    mod_labels = {"video": "视频", "image": "图像", "audio": "音频", "chat": "对话", "other": "其他"}
    html = """<!DOCTYPE html>
<html lang="__DEFLANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PowerTokens Usage Dashboard</title>
<style>
:root{--bg:#f5f7fa;--card:#ffffff;--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb;--est:#2563eb;--real:#16a34a;--vid:#6366f1;--img:#ec4899;--aud:#f59e0b;--chat:#10b981;--other:#9ca3af}
[data-theme=dark]{--bg:#0f172a;--card:#1e293b;--ink:#e2e8f0;--muted:#94a3b8;--line:#334155;--accent:#60a5fa;--est:#60a5fa;--real:#4ade80}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);padding:20px;line-height:1.5}
.wrap{max-width:1100px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:16px}
h1{font-size:20px;margin:0}
.sub{color:var(--muted);font-size:12px}
button{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi .v{font-size:22px;font-weight:700}
.kpi .l{font-size:12px;color:var(--muted);margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card h2{font-size:14px;margin:0 0 10px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card)}
.scroll{max-height:340px;overflow:auto}
.tag{padding:1px 7px;border-radius:10px;font-size:11px}
.t-succ{background:#dcfce7;color:#166534}.t-fail{background:#fee2e2;color:#991b1b}.t-unk{background:#f1f5f9;color:#475569}
.t-wb{background:#e0e7ff;color:#3730a3}.t-log{background:#fef3c7;color:#92400e}.t-srv{background:#dbeafe;color:#1e40af}
.empty{color:var(--muted);font-size:13px;padding:20px;text-align:center}
.fbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.fb.on{background:var(--accent);color:#fff;border-color:var(--accent)}
svg{width:100%;height:auto;display:block}
.tip{position:fixed;pointer-events:none;background:#111;color:#fff;font-size:11px;padding:4px 8px;border-radius:6px;opacity:0;transition:opacity .1s;z-index:9}
</style>
</head>
<body data-theme="__THEME__">
<div class="wrap">
<header>
  <div><h1 data-i18n="title">PowerTokens</h1><div class="sub" id="sub"></div></div>
  <div style="display:flex;gap:8px">
    <button onclick="toggleLang()" data-i18n="toggle_lang">EN</button>
    <button onclick="toggleTheme()" data-i18n="toggle_theme">Theme</button>
  </div>
</header>
<div class="sub" id="srcInfo" style="margin:-4px 0 10px"></div>
<div class="fbar" id="fbar"></div>
<div class="fbar" id="accbar"></div>
<div class="kpis" id="kpis"></div>
<div class="grid">
  <div class="card"><h2 data-i18n="trend_title">Cost Trend</h2><div id="lineChart"></div><div class="legend"><span><i style="background:var(--est)"></i><span data-i18n="legend_est">Estimated</span></span><span><i style="background:var(--real)"></i><span data-i18n="legend_real">Actual</span></span></div></div>
  <div class="card"><h2 data-i18n="donut_title">Modality</h2><div id="donut"></div></div>
  <div class="card" style="grid-column:1/-1"><h2 data-i18n="bar_title">By Model</h2><div id="barChart"></div></div>
</div>
<div class="card" style="margin-top:14px"><h2 data-i18n="hourgrid_title">Daily Activity</h2><div id="hourGrid"></div></div>
<div class="grid" style="margin-top:14px">
  <div class="card"><h2 data-i18n="ridge_title">Token Distribution</h2><div id="ridge"></div></div>
  <div class="card"><h2 data-i18n="hourly_title">Hourly by Model</h2><div id="hourlyModel"></div></div>
</div>
<div class="card" style="margin-top:14px"><h2 data-i18n="dailymodel_title">Daily Model Ratio</h2><div id="dailyModel"></div></div>
<div class="card" style="margin-top:14px"><h2 data-i18n="off_title">Official Stats</h2><div id="offInfo"></div></div>
<div class="card" style="margin-top:14px"><h2 data-i18n="key_title">By API Key</h2><div id="keyInfo"></div></div>
<div class="card" style="margin-top:14px"><h2 data-i18n="conv_title">Attribution</h2><div id="convInfo"></div></div>
<div class="card" style="margin-top:14px"><h2 data-i18n="tbl_title">Call Details</h2><div class="scroll"><table id="tbl"><thead><tr><th data-i18n="th_time">Time</th><th data-i18n="th_model">Model</th><th data-i18n="th_mod">Modality</th><th data-i18n="th_sec">Sec</th><th data-i18n="th_res">Res</th><th data-i18n="th_status">Status</th><th data-i18n="th_est">Est $</th><th data-i18n="th_real">Real $</th><th data-i18n="th_src">Source</th></tr></thead><tbody></tbody></table></div></div>
<div class="card" style="margin-top:14px" id="catalogCard"><h2 data-i18n="catalog_title">Catalog</h2><div id="catalogBody"></div></div>
</div>
<div class="tip" id="tip"></div>
<script>
const DATA = __DATA__;
let LANG = (localStorage.getItem("ptdash_lang") || "__DEF_LANG__");
const I18N = {
 en:{
  title:"PowerTokens Usage Dashboard", toggle_theme:"Theme", toggle_lang:"中文",
  sub_generated:"Generated {g} · {n} calls",
  trend_title:"Cost Trend (by day)", legend_est:"Estimated", legend_real:"Actual",
  donut_title:"Modality Breakdown", bar_title:"Cost by Model (Top)", src_title:"Data Sources",
  hourgrid_title:"Daily Activity (hour × date)",
  ridge_title:"Token Size Distribution per Request (by model, 50%/90% pct)",
  hourly_title:"0–24h Distribution · by Model",
  dailymodel_title:"Daily Model Usage Ratio (stacked calls)",
  off_title:"PT Console Official Stats (server)", key_title:"Cost by API Key (full)",
  conv_title:"Call ↔ Conversation Attribution", tbl_title:"Call Details (latest 200)",
  catalog_title:"PT Model Catalog (pt_models.json, optional)",
  kpi_cost:"Cost (selected range, server-exact)", kpi_calls:"Calls", kpi_tokens:"Total Tokens",
  kpi_io:"Input : Output", kpi_active:"Active Days", kpi_balance:"Account Balance",
  kpi_top:"Top Model", kpi_est:"Local Estimate", kpi_credits:"Remaining credits",
  empty_day:"No daily data", empty_range:"No data in this range",
  empty_tokens:"No token-detail records in this range (needs server data)", empty_mods:"No modality data",
  band:"0–8 AM {a}% · 8–12 AM {b}% · 12–18 PM {c}% · 18–24 PM {d}%",
  src_txt:"PT console {n} records (exact) · WorkBuddy logs {wb} · Recorder {log}.",
  src_auth:"Server data is authoritative; others are estimates.",
  src_no:"No server data — run fetch_pt_server.py (needs browser Cookie _Bearer).",
  off_spend:"Official total spend", off_req:"Requests", off_rate:"Success rate",
  off_credits:"credits balance ≈ $", off_no:"No server aggregate yet — run fetch_pt_server.py then regenerate",
  off_fetched:"Server data fetched at {t}",
  tbl_empty:"No call records — run pt_log.py, or confirm PT calls exist in WorkBuddy logs",
  key_empty:"None yet — run fetch_pt_server.py and join_conversations.py",
  key_hdr_model:"Key (masked)", key_hdr_calls:"Calls", key_hdr_amt:"Amount",
  key_hdr_quota:"quota", key_hdr_models:"Main models", key_hdr_last:"Last call",
  conv_empty:"None yet — run join_conversations.py", conv_match:"Calls attributed to sessions",
  conv_matched:"Attributed to conversations", conv_other:"Other clients (dsh/Trae/scripts)",
  conv_method:"Match: model name + nearest timestamp (±{w}s, one-to-one). Median |Δt| {m}s. Local & PT share no request_id, so fingerprint matching.",
  conv_top:"Top 15 sessions", catalog_note:"Showing first 40; from GET /v1/models snapshot.",
  cat_hdr_model:"Model", cat_hdr_type:"Type", cat_hdr_price:"Priced",
  all_accounts:"All accounts", r_24h:"24h", r_3d:"3d", r_7d:"7d", r_30d:"30d", r_90d:"90d", r_all:"All",
  mod_video:"Video", mod_image:"Image", mod_audio:"Audio", mod_chat:"Chat", mod_other:"Other",
  th_time:"Time", th_model:"Model", th_mod:"Modality", th_sec:"Sec", th_res:"Res",
  th_status:"Status", th_est:"Est $", th_real:"Real $", th_src:"Source",
  m_50:"p50", m_90:"p90", rec_unit:"rec", times_unit:"times", hour_unit:"h",
  ridge_note:"X-axis = total tokens per request (log scale); solid = p50 median, dashed = p90. Only calls with token details counted.",
  hourgrid_note:"Each cell = calls in that hour of that day; darker = denser.",
  src_console:"console"
 },
 zh:{
  title:"PowerTokens 用量看板", toggle_theme:"切换主题", toggle_lang:"EN",
  sub_generated:"生成于 {g} · 共 {n} 次调用",
  trend_title:"成本趋势（按天）", legend_est:"预估", legend_real:"真实",
  donut_title:"模态占比", bar_title:"按模型成本（Top）", src_title:"数据来源",
  hourgrid_title:"日活分布（小时 × 日期）",
  ridge_title:"单次请求 token 大小分布（按模型，50%/90% 分位）",
  hourly_title:"0-24 时分布 · 按模型", dailymodel_title:"每日模型用量比例（调用次数堆叠）",
  off_title:"PT 控制台官方统计（服务端）", key_title:"按 API Key 成本（全量精确）",
  conv_title:"调用 ↔ 对话 归因", tbl_title:"调用明细（最近 200 条）",
  catalog_title:"PT 模型目录对照（pt_models.json，可选）",
  kpi_cost:"消耗（所选范围，服务端精确）", kpi_calls:"调用次数", kpi_tokens:"总 Token",
  kpi_io:"输入 : 输出", kpi_active:"活跃天数", kpi_balance:"账户余额",
  kpi_top:"TOP 模型", kpi_est:"本地估算", kpi_credits:"剩余 credits",
  empty_day:"暂无按天数据", empty_range:"该范围内无数据",
  empty_tokens:"该范围内无含 token 明细的记录（需服务端数据）", empty_mods:"暂无模态数据",
  band:"凌晨0-8 {a}% · 上午8-12 {b}% · 下午12-18 {c}% · 晚18-24 {d}%",
  src_txt:"PT 控制台 {n} 条（精确）· WorkBuddy 日志 {wb} 条 · 记录器 {log} 条。",
  src_auth:"服务端为权威数据，其余是估算补充。",
  src_no:"无服务端数据 — 运行 fetch_pt_server.py 拉取（需浏览器 Cookie _Bearer）。",
  off_spend:"官方统计总花费", off_req:"请求数", off_rate:"成功率",
  off_credits:"credits 余额 ≈ $", off_no:"暂无服务端聚合数据 — 运行 fetch_pt_server.py 后再生成",
  off_fetched:"服务端数据抓取于 {t}",
  tbl_empty:"暂无调用记录 — 运行 pt_log.py 记一笔，或确认 WorkBuddy 日志里有 PT 调用",
  key_empty:"暂无 — 先跑 fetch_pt_server.py 与 join_conversations.py",
  key_hdr_model:"Key（掩码）", key_hdr_calls:"调用", key_hdr_amt:"金额",
  key_hdr_quota:"quota", key_hdr_models:"主要模型", key_hdr_last:"最近调用",
  conv_empty:"暂无 — 跑 join_conversations.py 生成归因", conv_match:"对上会话的调用占比",
  conv_matched:"已归因到对话", conv_other:"其它客户端（dsh/Trae/脚本）",
  conv_method:"匹配方式：模型名 + 时间戳就近（±{w}s，一对一占用），命中记录 |Δt| 中位数 {m}s。本地与 PT 无共享 request_id，故为指纹匹配。",
  conv_top:"Top 15 会话", catalog_note:"仅展示前 40 条；来自 GET /v1/models 快照。",
  cat_hdr_model:"模型", cat_hdr_type:"类型", cat_hdr_price:"定价",
  all_accounts:"全部账户", r_24h:"近24h", r_3d:"近3天", r_7d:"近7天", r_30d:"近30天", r_90d:"近90天", r_all:"全部",
  mod_video:"视频", mod_image:"图像", mod_audio:"音频", mod_chat:"对话", mod_other:"其他",
  th_time:"时间", th_model:"模型", th_mod:"模态", th_sec:"秒", th_res:"分辨率",
  th_status:"状态", th_est:"预估$", th_real:"真实$", th_src:"来源",
  m_50:"p50", m_90:"p90", rec_unit:"笔", times_unit:"次", hour_unit:"时",
  ridge_note:"横轴 = 单次请求 token 总量（对数轴）；实线 = p50 中位数，虚线 = p90。仅统计含 token 明细的调用。",
  hourgrid_note:"每格 = 该日该小时的调用次数，颜色越深越密集。",
  src_console:"控制台"
 }
};
function T(k,p){ const d=I18N[LANG]||I18N.en; let s=(d&&d[k]!=null)?d[k]:(I18N.en[k]!=null?I18N.en[k]:k); if(p){for(const j in p){ if(p[j]!=null) s=s.split("{"+j+"}").join(String(p[j])); } } return s; }
function modLabel(m){ return T("mod_"+m)||m; }
function applyI18n(){ document.querySelectorAll("[data-i18n]").forEach(e=>{ const k=e.getAttribute("data-i18n"); if(k) e.textContent=T(k); }); document.title=T("title"); document.documentElement.lang=(LANG==="zh")?"zh-CN":"en"; }
const MOD_COLOR={video:"var(--vid)",image:"var(--img)",audio:"var(--aud)",chat:"var(--chat)",other:"var(--other)"};
const fmt=n=>n==null?"-":"$"+Number(n).toFixed(4);
const k=DATA.kpi;
const RECS=DATA.recs||[];
const PALETTE=["#2563eb","#16a34a","#ec4899","#f59e0b","#6366f1","#10b981","#ef4444","#0ea5e9","#a855f7","#84cc16"];
let topModels=[];let activeDays=0;let activeAccount="__all__";
const BJ=8*3600;
function mColor(m){const i=topModels.indexOf(m);return i>=0?PALETTE[i%PALETTE.length]:"var(--muted)";}
function dayOf(ts){const d=new Date((ts+BJ)*1000);return d.getUTCFullYear()+"-"+String(d.getUTCMonth()+1).padStart(2,"0")+"-"+String(d.getUTCDate()).padStart(2,"0");}
function hourOf(ts){return new Date((ts+BJ)*1000).getUTCHours();}
function fmtTok(n){if(!n)return "0"; if(LANG==="zh"){ if(n>=1e8)return (n/1e8).toFixed(2)+"亿"; if(n>=1e4)return (n/1e4).toFixed(1)+"万"; return String(Math.round(n)); } else { if(n>=1e6)return (n/1e6).toFixed(2)+"M"; if(n>=1e3)return (n/1e3).toFixed(1)+"K"; return String(Math.round(n)); } }
function fmtD(n){return "$"+Number(n||0).toFixed(2);}
function pct(arr,q){const a=arr.slice().sort((x,y)=>x-y);return a[Math.min(a.length-1,Math.floor(q*(a.length-1)))];}
function filtered(){let f=RECS;if(activeAccount&&activeAccount!=="__all__")f=f.filter(r=>r[7]===activeAccount);if(activeDays){const cut=Math.floor(Date.now()/1000)-activeDays*86400;f=f.filter(r=>r[0]>=cut);}return f;}
function balTxt(){const b=DATA.server&&DATA.server.balance;return b?"$"+Number(b.equal_usd||0).toFixed(4):"-";}
function renderKpis(F){
 let calls=F.length,cost=0,tin=0,tout=0;const days=new Set();
 F.forEach(r=>{cost+=r[4];tin+=r[2];tout+=r[3];days.add(dayOf(r[0]));});
 let rows;
 if(RECS.length){
  rows=[[T("kpi_cost"),fmtD(cost)],[T("kpi_calls"),calls],[T("kpi_tokens"),fmtTok(tin+tout)],
   [T("kpi_io"),(tout?Math.round(tin/tout):0)+" : 1"],[T("kpi_active"),days.size],[T("kpi_balance"),balTxt()]];
 }else{
  rows=[[T("kpi_cost"),fmt(k.real)+" ("+k.real_n+" "+T("rec_unit")+")"],[T("kpi_calls"),k.calls],[T("kpi_top"),k.top_model],
   [T("kpi_est"),"$"+Number(k.est).toFixed(4)+" ("+k.est_n+" "+T("rec_unit")+")"],[T("kpi_balance"),balTxt()],
   [T("kpi_credits"),(DATA.server&&DATA.server.balance)?DATA.server.balance.credits:"-"]];
 }
 document.getElementById("kpis").innerHTML=rows.map(x=>`<div class="kpi"><div class="v">${x[1]}</div><div class="l">${x[0]}</div></div>`).join("");
}
function renderTrend(F){
 const el=document.getElementById("lineChart");
 let d;
 if(RECS.length){
  const m={};F.forEach(r=>{const dd=dayOf(r[0]);const o=m[dd]=m[dd]||{est:0,real:0};o.real+=r[4];o.est+=r[5];});
  d=Object.keys(m).sort().map(x=>({day:x,est:m[x].est,real:m[x].real}));
 }else d=DATA.daily;
 if(!d.length){el.innerHTML='<div class="empty">'+T("empty_day")+'</div>';return;}
 const W=520,H=200,pad=30;const max=Math.max(1,...d.map(x=>x.est+x.real));
 const sx=i=>pad+(W-2*pad)*i/(d.length-1||1);
 const sy=v=>H-pad-(H-2*pad)*v/max;
 let s1="M",s2="M";
 d.forEach((x,i)=>{s1+=(i?"L":"")+sx(i).toFixed(1)+","+sy(x.est).toFixed(1);s2+=(i?"L":"")+sx(i).toFixed(1)+","+sy(x.real).toFixed(1);});
 let dots="";
 d.forEach((x,i)=>{dots+=`<circle cx="${sx(i).toFixed(1)}" cy="${sy(x.est).toFixed(1)}" r="3" fill="var(--est)"><title>${x.day} ${T("legend_est")} ${fmt(x.est)}</title></circle>`;
   dots+=`<circle cx="${sx(i).toFixed(1)}" cy="${sy(x.real).toFixed(1)}" r="3" fill="var(--real)"><title>${x.day} ${T("legend_real")} ${fmt(x.real)}</title></circle>`;});
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}"><path d="${s1}" fill="none" stroke="var(--est)" stroke-width="2"/><path d="${s2}" fill="none" stroke="var(--real)" stroke-width="2" stroke-dasharray="4 3"/>${dots}
  <line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="var(--line)"/></svg>`;
}
function renderHourGrid(F){
 const el=document.getElementById("hourGrid");
 if(!F.length){el.innerHTML='<div class="empty">'+T("empty_range")+'</div>';return;}
 const map={};const bands=[0,0,0,0];
 F.forEach(r=>{const d=dayOf(r[0]),h=hourOf(r[0]);if(!map[d])map[d]=new Array(24).fill(0);map[d][h]++;bands[h<8?0:h<12?1:h<18?2:3]++;});
 const days=Object.keys(map).sort();const n=F.length;
 const bandTxt=T("band",{a:(100*bands[0]/n).toFixed(1),b:(100*bands[1]/n).toFixed(1),c:(100*bands[2]/n).toFixed(1),d:(100*bands[3]/n).toFixed(1)});
 const W=1060,padL=78,cw=(W-padL-6)/24,ch=13,rh=15,padT=18;
 const H=padT+days.length*rh+8;
 let maxC=1;days.forEach(d=>map[d].forEach(c=>{if(c>maxC)maxC=c;}));
 let g=`<text x="${padL}" y="12" font-size="11" fill="var(--muted)">${bandTxt}</text>`;
 days.forEach((d,i)=>{
  const y=padT+i*rh;
  g+=`<text x="${padL-6}" y="${y+ch}" text-anchor="end" font-size="10" fill="var(--muted)">${d.slice(5)}</text>`;
  for(let h=0;h<24;h++){const c=map[d][h];const a=c?(0.12+0.88*Math.sqrt(c/maxC)).toFixed(2):"0.05";
   g+=`<rect x="${(padL+h*cw).toFixed(1)}" y="${y}" width="${(cw-1.5).toFixed(1)}" height="${ch}" rx="2" fill="rgba(37,99,235,${a})"><title>${d} ${h}:00-${h+1}:00 · ${c} ${T("times_unit")}</title></rect>`;}
 });
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${g}</svg><div class="sub">${T("hourgrid_note")}</div>`;
}
function renderRidge(F){
 const el=document.getElementById("ridge");
 const byM={};
 F.forEach(r=>{const t=r[2]+r[3];if(t>0)(byM[r[1]]=byM[r[1]]||[]).push(t);});
 let mods=Object.keys(byM).filter(m=>byM[m].length>=3).sort((a,b)=>byM[b].length-byM[a].length).slice(0,8);
 if(!mods.length){el.innerHTML='<div class="empty">'+T("empty_tokens")+'</div>';return;}
 let lo=Infinity,hi=0;
 mods.forEach(m=>byM[m].forEach(v=>{if(v<lo)lo=v;if(v>hi)hi=v;}));
 lo=Math.log10(1+lo);hi=Math.log10(1+hi);
 const W=520,H=mods.length*36+34,padL=108,padR=100,padT=10;
 const plotW=W-padL-padR;
 const xw=v=>padL+plotW*(Math.log10(1+v)-lo)/((hi-lo)||1);
 let g="";
 mods.forEach((m,i)=>{
  const vals=byM[m];const base=padT+i*36+30;
  const p50=pct(vals,0.5),p90=pct(vals,0.9);
  const B=36;const hist=new Array(B).fill(0);
  vals.forEach(v=>{const b=Math.min(B-1,Math.floor((Math.log10(1+v)-lo)/((hi-lo)||1)*B));hist[b]++;});
  const hmax=Math.max(1,...hist);
  hist.forEach((c,b)=>{
   if(!c)return;
   const x0=xw(Math.pow(10,lo+(hi-lo)*b/B)-1),x1=xw(Math.pow(10,lo+(hi-lo)*(b+1)/B)-1);
   const h=26*c/hmax;
   g+=`<rect x="${x0.toFixed(1)}" y="${(base-h).toFixed(1)}" width="${Math.max(1,x1-x0).toFixed(1)}" height="${h.toFixed(1)}" fill="${mColor(m)}" opacity="0.55"/>`;
  });
  g+=`<text x="${padL-6}" y="${base-8}" text-anchor="end" font-size="11" fill="var(--ink)">${m.slice(0,16)}</text>`;
  g+=`<text x="${padL-6}" y="${base+5}" text-anchor="end" font-size="10" fill="var(--muted)">${vals.length}${T("times_unit")}</text>`;
  g+=`<line x1="${xw(p50).toFixed(1)}" y1="${base-28}" x2="${xw(p50).toFixed(1)}" y2="${base}" stroke="var(--ink)" stroke-width="1" opacity="0.6"/>`;
  g+=`<line x1="${xw(p90).toFixed(1)}" y1="${base-28}" x2="${xw(p90).toFixed(1)}" y2="${base}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 2"/>`;
  g+=`<text x="${W-padR+6}" y="${base-12}" font-size="10" fill="var(--ink)">${T("m_50")} ${fmtTok(p50)}</text>`;
  g+=`<text x="${W-padR+6}" y="${base+2}" font-size="10" fill="var(--muted)">${T("m_90")} ${fmtTok(p90)}</text>`;
 });
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${g}</svg><div class="sub">${T("ridge_note")}</div>`;
}
function renderHourlyModel(F){
 const el=document.getElementById("hourlyModel");
 const tm=topModels.slice(0,8);
 if(!F.length||!tm.length){el.innerHTML='<div class="empty">'+T("empty_range")+'</div>';return;}
 const sums=tm.map(()=>new Array(24).fill(0));
 F.forEach(r=>{const i=tm.indexOf(r[1]);if(i>=0)sums[i][hourOf(r[0])]++;});
 const W=520,H=210,pad=26;
 const max=Math.max(1,...Array.from({length:24},(_,h)=>sums.reduce((a,s)=>a+s[h],0)));
 const x=h=>pad+(W-2*pad)*h/23;const y=v=>H-pad-(H-2*pad)*v/max;
 let paths="";let cum=new Array(24).fill(0);
 tm.forEach((m,i)=>{
  const up=cum.map((c,h)=>c+sums[i][h]);
  let p=up.map((v,h)=>(h?"L":"M")+x(h).toFixed(1)+","+y(v).toFixed(1)).join("");
  for(let h=23;h>=0;h--)p+="L"+x(h).toFixed(1)+","+y(cum[h]).toFixed(1);
  paths+=`<path d="${p}Z" fill="${mColor(m)}" opacity="0.7"/>`;
  cum=up;
 });
 let ax="";
 [0,6,12,18,23].forEach(h=>ax+=`<text x="${x(h).toFixed(1)}" y="${H-8}" font-size="10" text-anchor="middle" fill="var(--muted)">${h}${T("hour_unit")}</text>`);
 const leg=tm.map(m=>`<span><i style="background:${mColor(m)}"></i>${m.slice(0,18)}</span>`).join("");
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${paths}<line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="var(--line)"/>${ax}</svg><div class="legend">${leg}</div>`;
}
function renderDailyModel(F){
 const el=document.getElementById("dailyModel");
 const tm=topModels.slice(0,8);
 if(!F.length||!tm.length){el.innerHTML='<div class="empty">'+T("empty_range")+'</div>';return;}
 const days=[...new Set(F.map(r=>dayOf(r[0])))].sort();
 const sums=days.map(()=>new Array(tm.length+1).fill(0));
 F.forEach(r=>{const di=days.indexOf(dayOf(r[0]));const mi=tm.indexOf(r[1]);sums[di][mi<0?tm.length:mi]++;});
 const W=520,H=210,pad=26;
 const max=Math.max(1,...days.map((_,i)=>sums[i].reduce((a,b)=>a+b,0)));
 const bw=Math.max(3,(W-2*pad)/days.length*0.72);
 let g="";
 const step=Math.max(1,Math.ceil(days.length/10));
 days.forEach((d,i)=>{const x0=pad+(W-2*pad)*(i+0.5)/days.length-bw/2;let y=H-pad;
  sums[i].forEach((c,mi)=>{if(!c)return;const h=(H-2*pad)*c/max;y-=h;
   g+=`<rect x="${x0.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${mi<tm.length?mColor(tm[mi]):"var(--other)"}" opacity="0.85"><title>${d} · ${mi<tm.length?tm[mi]:T("mod_other")} · ${c} ${T("times_unit")}</title></rect>`;});
  if(i%step===0)g+=`<text x="${(x0+bw/2).toFixed(1)}" y="${H-8}" font-size="9" text-anchor="middle" fill="var(--muted)">${d.slice(5)}</text>`;
 });
 const leg=tm.map(m=>{let tc=0;F.forEach(r=>{if(r[1]===m)tc++;});return `<span><i style="background:${mColor(m)}"></i>${m.slice(0,16)} ${tc}${T("times_unit")}</span>`;}).join("");
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}"><line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="var(--line)"/>${g}</svg><div class="legend">${leg}</div>`;
}
const RANGES=[[1,"r_24h"],[3,"r_3d"],[7,"r_7d"],[30,"r_30d"],[90,"r_90d"],[0,"r_all"]];
function renderAccounts(){
 const el=document.getElementById("accbar");
 if(!DATA.accounts||DATA.accounts.length<2){el.style.display="none";return;}
 el.style.display="flex";
 const all=DATA.accounts.reduce((a,x)=>({calls:a.calls+x.calls,real:a.real+x.real}),{calls:0,real:0});
 let h=`<button class="fb ${activeAccount==='__all__'?'on':''}" data-a="__all__">${T("all_accounts")} · $${all.real.toFixed(2)} · ${all.calls}${T("times_unit")}</button>`;
 h+=DATA.accounts.map(x=>`<button class="fb ${activeAccount===x.name?'on':''}" data-a="${x.name}">${x.name} · $${x.real.toFixed(2)} · ${x.calls}${T("times_unit")}</button>`).join("");
 el.innerHTML=h;
 el.querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>{activeAccount=b.dataset.a;renderAccounts();renderAll();}));
}
function renderFbar(){
 const el=document.getElementById("fbar");
 el.innerHTML=RANGES.map(r=>`<button class="fb ${activeDays===r[0]?"on":""}" data-d="${r[0]}">${T(r[1])}</button>`).join("");
 el.querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>{activeDays=+b.dataset.d;renderAll();}));
}
function renderAll(){
 const F=filtered();
 const c={};F.forEach(r=>c[r[1]]=(c[r[1]]||0)+1);
 topModels=Object.keys(c).sort((a,b)=>c[b]-c[a]).slice(0,10);
 renderKpis(F);renderTrend(F);renderHourGrid(F);renderRidge(F);renderHourlyModel(F);renderDailyModel(F);
}
function renderBar(){
 const el=document.getElementById("barChart");const m=DATA.models.slice(0,12);
 if(!m.length){el.innerHTML='<div class="empty">'+T("empty_range")+'</div>';return;}
 const W=520,H=Math.max(160,m.length*22+20),pad=90,barH=16;
 const max=Math.max(1,...m.map(x=>x.est+x.real));
 let rows="";
 m.forEach((x,i)=>{const y=10+i*(barH+4);const w=(W-pad-10)*(x.est+x.real)/max;
   rows+=`<g><text x="${pad-6}" y="${y+barH/2+4}" text-anchor="end" font-size="11" fill="var(--ink)">${x.model}</text>
   <rect x="${pad}" y="${y}" width="${w}" height="${barH}" rx="3" fill="var(--accent)"><title></title></rect>
   <text x="${pad+w+4}" y="${y+barH/2+4}" font-size="10" fill="var(--muted)">${fmt(x.est+x.real)}</text></g>`;});
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${rows}</svg>`;
 const rs=el.querySelectorAll("rect");
 m.forEach((x,i)=>{rs[i].addEventListener("mousemove",e=>showTip(e,x.model+" · "+x.calls+" "+T("times_unit")+" · "+T("legend_est")+fmt(x.est)+" "+T("legend_real")+fmt(x.real)));rs[i].addEventListener("mouseleave",hideTip);});
}
function renderDonut(){
 const el=document.getElementById("donut");const md=DATA.mods;
 const tot=md.reduce((a,b)=>a+b.calls,0);
 if(!tot){el.innerHTML='<div class="empty">'+T("empty_mods")+'</div>';return;}
 const W=200,R=80,C=100;let ang=-Math.PI/2;let segs="";let leg="";
 md.forEach(x=>{const a2=ang+2*Math.PI*x.calls/tot;const large=(a2-ang)>Math.PI?1:0;
   const x1=C+R*Math.cos(ang),y1=C+R*Math.sin(ang),x2=C+R*Math.cos(a2),y2=C+R*Math.sin(a2);
   segs+=`<path d="M${C},${C} L${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} Z" fill="${MOD_COLOR[x.mod]||'var(--other)'}"><title></title></path>`;
   leg+=`<div><i style="background:${MOD_COLOR[x.mod]||'var(--other)'}"></i>${modLabel(x.mod)} ${x.calls} (${(100*x.calls/tot).toFixed(0)}%)</div>`;
   ang=a2;});
 el.innerHTML=`<div style="display:flex;gap:12px;align-items:center"><svg viewBox="0 0 200 200" style="width:160px;height:160px">${segs}<circle cx="100" cy="100" r="42" fill="var(--card)"/></svg><div class="legend" style="flex-direction:column">${leg}</div></div>`;
 const ps=el.querySelectorAll("path");
 md.forEach((x,i)=>{ps[i].addEventListener("mousemove",e=>showTip(e,modLabel(x.mod)+" "+x.calls+" "+T("times_unit")+" · "+T("legend_est")+fmt(x.est)));ps[i].addEventListener("mouseleave",hideTip);});
}
function renderOff(){
  const el=document.getElementById("offInfo");const d=(DATA.server&&DATA.server.dashboard)||null;const b=DATA.server&&DATA.server.balance;
  const rank=(DATA.server&&DATA.server.rank)||[];
  let html="";
  if(d&&d.top_data){
    const t=d.top_data;
    html+=`<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:10px">
    <div class="kpi"><div class="v">$${Number(t.spend_usd??t.spend/1000).toFixed(2)}</div><div class="l">${T("off_spend")}</div></div>
    <div class="kpi"><div class="v">${t.requests??"-"}</div><div class="l">${T("off_req")}</div></div>
    <div class="kpi"><div class="v">${t.success_rate!=null?t.success_rate:"-"}</div><div class="l">${T("off_rate")}</div></div>
    ${b?`<div class="kpi"><div class="v">${b.credits}</div><div class="l">${T("off_credits")}${Number(b.equal_usd).toFixed(4)}</div></div>`:""}
    </div>`;
  }
  if(rank.length){
    html+=`<table><thead><tr><th>${T("cat_hdr_model")}（${T("off_title")}）</th><th>quota</th><th>${T("kpi_cost")}</th></tr></thead><tbody>`+
      rank.slice(0,10).map(x=>`<tr><td>${x.model_id}</td><td>${x.sale_quota}</td><td>$${Number(x.amount).toFixed(4)}</td></tr>`).join("")+
      `</tbody></table>`;
  }
  if(!html) html='<div class="empty">'+T("off_no")+'</div>';
  if(DATA.server&&DATA.server.fetched_at) html+=`<div class="sub" style="margin-top:8px">${T("off_fetched",{t:DATA.server.fetched_at})}</div>`;
  el.innerHTML=html;
}
function renderTable(){
 const tb=document.querySelector("#tbl tbody");
 if(!DATA.recent.length){tb.innerHTML='<tr><td colspan="9" class="empty">'+T("tbl_empty")+'</td></tr>';return;}
 tb.innerHTML=DATA.recent.map(r=>`<tr>
  <td>${r.ts}</td><td>${r.model}</td><td>${modLabel(r.mod)}</td>
  <td>${r.sec??"-"}</td><td>${r.res??"-"}</td>
  <td><span class="tag ${r.status==='success'?'t-succ':r.status==='failed'?'t-fail':'t-unk'}">${r.status}</span></td>
  <td>${r.est==null?'-':"$"+Number(r.est).toFixed(4)}</td>
  <td>${r.real==null?'-':"$"+Number(r.real).toFixed(4)}</td>
  <td><span class="tag ${r.src==='server'?'t-srv':r.src==='workbuddy'?'t-wb':'t-log'}">${r.src}</span></td></tr>`).join("");
}
function renderKeys(){
  const el=document.getElementById("keyInfo");const keys=(DATA.conv&&DATA.conv.keys)||[];
  if(!keys.length){el.innerHTML='<div class="empty">'+T("key_empty")+'</div>';return;}
  const rows=keys.map(k=>`<tr><td><code>${k.key}</code></td><td style="text-align:right">${k.calls}</td>
    <td style="text-align:right"><b>$${Number(k.amount).toFixed(4)}</b></td>
    <td style="text-align:right">${k.quota}</td>
    <td>${(k.models||[]).map(m=>m[0]).join(", ").slice(0,40)}</td>
    <td>${k.last||"-"}</td></tr>`).join("");
  el.innerHTML=`<div class="scroll"><table><thead><tr><th>${T("key_hdr_model")}</th><th style="text-align:right">${T("key_hdr_calls")}</th><th style="text-align:right">${T("key_hdr_amt")}</th><th style="text-align:right">${T("key_hdr_quota")}</th><th>${T("key_hdr_models")}</th><th>${T("key_hdr_last")}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function renderConv(){
  const el=document.getElementById("convInfo");const c=DATA.conv||{};
  const s=c.stat, t=c.totals||{}, sess=c.sessions||[];
  if(!s){el.innerHTML='<div class="empty">'+T("conv_empty")+'</div>';return;}
  let html=`<div class="kpis" style="margin-bottom:12px">
   <div class="kpi"><div class="v">${s.match_rate}%</div><div class="l">${T("conv_match")}</div></div>
   <div class="kpi"><div class="v">$${Number(t.matched_amount||0).toFixed(2)}</div><div class="l">${T("conv_matched")}</div></div>
   <div class="kpi"><div class="v">$${Number(t.unmatched_amount||0).toFixed(2)}</div><div class="l">${T("conv_other")}</div></div>
  </div>
  <div class="sub" style="margin-bottom:8px">${T("conv_method",{w:s.window,m:s.dt_median})}</div>`;
  const rows=sess.slice(0,15).map(x=>`<tr><td>${x.first||"-"}</td><td title="${(x.cwd||'').replace(/"/g,'')}">${(x.cwd||"-").split(String.fromCharCode(92)).pop()||"-"}</td>
    <td><code>${(x.session||"").slice(0,8)}</code></td><td style="text-align:right">${x.calls}</td>
    <td style="text-align:right"><b>$${Number(x.amount).toFixed(4)}</b></td>
    <td>${(x.models||[]).map(m=>m[0]).join(", ").slice(0,32)}</td></tr>`).join("");
  html+=`<div class="scroll"><table><thead><tr><th>${T("th_time")}</th><th>${T("th_mod")}</th><th>${T("th_status")}</th><th style="text-align:right">${T("key_hdr_calls")}</th><th style="text-align:right">${T("key_hdr_amt")}</th><th>${T("cat_hdr_model")}</th></tr></thead><tbody>${rows||'<tr><td colspan="6" class="empty">-</td></tr>'}</tbody></table></div>`;
  if(c.generated) html+=`<div class="sub" style="margin-top:8px">${T("conv_top")} · ${c.generated}</div>`;
  el.innerHTML=html;
}
function renderCatalog(){
 const el=document.getElementById("catalogBody");const c=DATA.catalog;
 if(!c){document.getElementById("catalogCard").style.display="none";return;}
 const rows=c.slice(0,40).map(m=>`<tr><td>${m.id||m.name||'-'}</td><td>${(m.type||m.modality||'-')}</td><td>${(m.priced?'✔':'-')}</td></tr>`).join("");
 el.innerHTML=`<table><thead><tr><th>${T("cat_hdr_model")}</th><th>${T("cat_hdr_type")}</th><th>${T("cat_hdr_price")}</th></tr></thead><tbody>${rows}</tbody></table><div class="sub">${T("catalog_note")}</div>`;
}
function renderSrc(){
 const n=DATA.server_n||0;
 document.getElementById("srcInfo").innerHTML=T("src_txt",{n:n, wb:k.wb, log:k.log})+(n?(" "+T("src_auth")):(" "+T("src_no")));
}
function renderAllPages(){
 document.getElementById("sub").textContent=T("sub_generated",{g:DATA.generated,n:DATA.kpi.calls});
 renderFbar();renderAccounts();renderAll();renderBar();renderDonut();renderOff();renderTable();renderKeys();renderConv();renderCatalog();renderSrc();applyI18n();
}
function toggleTheme(){const b=document.body;const n=b.getAttribute("data-theme")==="dark"?"light":"dark";b.setAttribute("data-theme",n);localStorage.setItem("ptdash_theme",n);}
function toggleLang(){LANG=(LANG==="zh")?"en":"zh";localStorage.setItem("ptdash_lang",LANG);renderAllPages();}
// tooltip
const tip=document.getElementById("tip");
function showTip(e,t){tip.textContent=t;tip.style.opacity=1;tip.style.left=(e.clientX+12)+"px";tip.style.top=(e.clientY+12)+"px";}
function hideTip(){tip.style.opacity=0;}
(function(){const t=localStorage.getItem("ptdash_theme");if(t)document.body.setAttribute("data-theme",t);})();
renderAllPages();
</script>
</body>
</html>"""
    html = html.replace("__THEME__", theme or "light").replace("__DATA__", data_json)\
        .replace("__DEF_LANG__", lang).replace("__DEFLANG__", "zh-CN" if lang == "zh" else "en")
    return html


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="PowerTokens 用量看板生成器")
    ap.add_argument("--out", default="pt-usage-dashboard.html", help="输出 HTML 路径")
    ap.add_argument("--calls", default=os.path.join(skill_dir, "pt_calls.jsonl"), help="记录器日志路径")
    ap.add_argument("--projects", default=os.path.join(os.path.expanduser("~"), ".workbuddy", "projects"), help="WorkBuddy 日志目录")
    ap.add_argument("--catalog", default=os.path.join(skill_dir, "pt_models.json"), help="PT 模型目录 JSON")
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    ap.add_argument("--lang", default="en", choices=["en", "zh"], help="界面语言 / UI language（默认英文 en）")
    ap.add_argument("--server", default="", help="指定单个 PT 控制台缓存文件（默认自动 glob pt_usage_server*.json 全合并）")
    ap.add_argument("--no-server", action="store_true", help="不使用服务端数据")
    ap.add_argument("--conv", default=os.path.join(skill_dir, "pt_conversation_map.json"), help="调用↔对话归因（join_conversations.py 产出）")
    ap.add_argument("--no-wb", action="store_true", help="不扫描 WorkBuddy 日志")
    args = ap.parse_args()

    conv = load_conversation_map(args.conv) or {}
    server_meta = {}
    recs = []
    accounts_set = {}
    if not args.no_server:
        if args.server:
            server_files = [args.server] if os.path.isfile(args.server) else []
            if not server_files:
                print("[warn] 指定的 --server 文件不存在: %s" % args.server)
        else:
            server_files = sorted(glob.glob(os.path.join(skill_dir, "pt_usage_server*.json")))
        for fp in server_files:
            if not os.path.exists(fp):
                continue
            # 优先用文件内的 account 字段，否则从文件名推断
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
            recs_server, meta = load_server_usage(fp, acc)
            recs += recs_server
            ac = accounts_set.setdefault(acc, {"calls": 0, "real": 0.0})
            ac["calls"] += len(recs_server)
            ac["real"] += sum(r.get("cost_real") or 0 for r in recs_server)
            if not server_meta and meta:
                server_meta = meta
    accounts_list = [{"name": n, "calls": v["calls"], "real": round(v["real"], 4)} for n, v in accounts_set.items()]
    recs += load_logger_calls(args.calls)
    if not args.no_wb:
        recs += load_workbuddy_logs(args.projects)
    catalog = load_catalog(args.catalog)
    agg = aggregate(recs)

    html = render_html(skill_dir, recs, agg, catalog, args.theme, server_meta, conv, accounts=accounts_list, lang=args.lang)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)

    k = agg
    print(f"[pt-usage-dashboard] 记录总数={len(recs)} "
          f"(server={sum(1 for r in recs if r['source']=='server')}, "
          f"logger={sum(1 for r in recs if r['source']=='logger')}, "
          f"workbuddy={sum(1 for r in recs if r['source']=='workbuddy')})")
    print(f"[pt-usage-dashboard] 预估合计=${agg['total_est']:.4f} ({agg['est_n']}笔)  真实合计=${agg['total_real']:.4f} ({agg['real_n']}笔)")
    print(f"[pt-usage-dashboard] 已写出 -> {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
