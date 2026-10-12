# -*- coding: utf-8 -*-
"""把 PT 的「逐笔调用」对上「本机对话/会话」，并输出按 API key 的拆分。

先看具体场景：你在 PT 后台看到一笔 $0.03 的扣费，只写着 model 和时间，
不知道是哪次聊天花的。本脚本把它对回到具体会话（sessionId + 工作目录），
于是你能看到「2026-09-02 那次 d:\\trae001\\pt-test 的排查会话，一共烧了 $X」。

匹配原理（PT 与本地没有共享 ID，只能靠「指纹」对齐）：
  - 本机会话侧：WorkBuddy 日志 ~/.workbuddy/projects/**/*.jsonl 中带 usage/rawUsage 的记录
    = 一次真正计费的模型调用，含 sessionId / cwd / timestamp / model。
  - PT 侧：/usage-records/list 的每条 amount，含 model_id / consume_time。
  - 对齐键：模型名（去掉本地的 `custom-local:` 前缀后比对）+ 时间戳就近（默认 ±120s）。
  - 用「贪心最近邻、一对一占用」避免多笔抢同一条会话记录。
  - 时间差越小越可信；实测匹配上的 |Δt| 中位数 ≈ 0.8s，属于强命中。

匹配不到的部分属正常：dsh / Trae / pt CLI / 自建脚本的调用不进 WorkBuddy 日志，
会归入 "未归因（其它客户端）"，看板上单独列出，不混进会话成本。

产出：<skill>/pt_conversation_map.json
  按 API key 汇总（全量精确）+ 按会话汇总（仅已匹配部分）+ 匹配率统计。
"""
import argparse, json, os, sys, glob, datetime
from collections import defaultdict, Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DISPLAY_TZ = 8 * 3600
DEFAULT_WINDOW = 120  # 秒


def norm_model(m):
    m = (m or "").strip()
    if m.startswith("custom-local:"):
        m = m.split(":", 1)[1]
    return m.lower()


def load_local_calls(projects_dir):
    """本机真正计费的模型调用（带 usage/rawUsage 的记录）。"""
    rows = []
    if not os.path.isdir(projects_dir):
        return rows
    for fp in glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True):
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
                    pd = o.get("providerData")
                    if not isinstance(pd, dict):
                        continue
                    if "usage" not in pd and "rawUsage" not in pd:
                        continue
                    t = o.get("timestamp")
                    if not isinstance(t, (int, float)):
                        continue
                    t = t / 1000.0 if t > 1e12 else t
                    rows.append({
                        "ts": t,
                        "model": norm_model(pd.get("requestModelId") or pd.get("model")),
                        "raw_model": pd.get("requestModelId") or pd.get("model"),
                        "session": o.get("sessionId"),
                        "cwd": o.get("cwd"),
                        "file": fp,
                    })
        except Exception:
            continue
    return rows


def join(pt_records, local_rows, window=DEFAULT_WINDOW):
    """贪心最近邻一对一匹配：同模型 + 时间差在窗口内取最近的。"""
    by_model = defaultdict(list)
    for x in local_rows:
        by_model[x["model"]].append(x)
    for k in by_model:
        by_model[k].sort(key=lambda z: z["ts"])

    used = set()
    links = []
    dts = []
    for r in sorted(pt_records, key=lambda z: z.get("consume_time") or 0):
        ts = r.get("consume_time")
        if not ts:
            continue
        m = norm_model(r.get("model_id"))
        best = None
        for x in by_model.get(m, []):
            d = x["ts"] - ts
            if d > window:
                break
            if abs(d) > window:
                continue
            key = id(x)
            if key in used:
                continue
            if best is None or abs(d) < abs(best[1]):
                best = (x, d, key)
        if best:
            x, d, key = best
            used.add(key)
            dts.append(abs(d))
            links.append({
                "consume_id": r.get("consume_id"),
                "request_id": r.get("request_id"),
                "consume_time": ts,
                "model": r.get("model_id"),
                "amount": r.get("amount"),
                "quota": r.get("quota"),
                "api_key_masked": r.get("api_key_masked"),
                "session": x["session"],
                "cwd": x["cwd"],
                "dt": round(d, 2),
            })
    return links, dts


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="PT 调用 ↔ 本机对话 归因 + 按 API key 拆分")
    ap.add_argument("--server", default=os.path.join(skill_dir, "pt_usage_server.json"))
    ap.add_argument("--projects", default=os.path.join(os.path.expanduser("~"), ".workbuddy", "projects"))
    ap.add_argument("--out", default=os.path.join(skill_dir, "pt_conversation_map.json"))
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="时间匹配窗口（秒）")
    args = ap.parse_args()

    if not os.path.exists(args.server):
        sys.stderr.write("缺少 %s，先跑 fetch_pt_server.py\n" % args.server)
        sys.exit(2)
    blob = json.load(open(args.server, encoding="utf-8"))
    pt = blob.get("records") or []

    print("[1/3] 扫描本机会话日志 ...")
    local = load_local_calls(args.projects)
    print("      可计费调用行数: %d" % len(local))

    print("[2/3] 匹配（模型 + 时间窗口 ±%ds）..." % args.window)
    links, dts = join(pt, local, args.window)
    dts_sorted = sorted(dts)
    stat = {
        "pt_records": len(pt),
        "matched": len(links),
        "match_rate": round(100.0 * len(links) / len(pt), 2) if pt else 0.0,
        "dt_median": round(dts_sorted[len(dts_sorted) // 2], 2) if dts_sorted else None,
        "dt_p90": round(dts_sorted[int(len(dts_sorted) * 0.9)], 2) if dts_sorted else None,
        "window": args.window,
    }
    print("      命中 %d / %d (%.1f%%) | |Δt| 中位数 %ss" %
          (stat["matched"], stat["pt_records"], stat["match_rate"], stat["dt_median"]))

    # --- 按 API key（全量，精确） ---
    by_key = defaultdict(lambda: {"calls": 0, "amount": 0.0, "quota": 0, "models": Counter(),
                                  "first": None, "last": None})
    for r in pt:
        k = r.get("api_key_masked") or "-"
        b = by_key[k]
        b["calls"] += 1
        b["amount"] += float(r.get("amount") or 0)
        b["quota"] += int(r.get("quota") or 0)
        b["models"][r.get("model_id")] += 1
        t = r.get("consume_time") or 0
        b["first"] = t if b["first"] is None else min(b["first"], t)
        b["last"] = t if b["last"] is None else max(b["last"], t)
    keys = []
    for k, v in sorted(by_key.items(), key=lambda kv: -kv[1]["amount"]):
        keys.append({
            "key": k, "calls": v["calls"], "amount": round(v["amount"], 4), "quota": v["quota"],
            "models": v["models"].most_common(5),
            "first": datetime.datetime.fromtimestamp(v["first"] + DISPLAY_TZ, datetime.UTC).strftime("%Y-%m-%d %H:%M") if v["first"] else None,
            "last": datetime.datetime.fromtimestamp(v["last"] + DISPLAY_TZ, datetime.UTC).strftime("%Y-%m-%d %H:%M") if v["last"] else None,
        })

    # --- 按会话（仅已匹配） ---
    by_sess = defaultdict(lambda: {"calls": 0, "amount": 0.0, "models": Counter(),
                                   "first": None, "last": None, "cwd": None, "keys": Counter()})
    for l in links:
        s = l["session"] or "(无 sessionId)"
        b = by_sess[s]
        b["calls"] += 1
        b["amount"] += float(l["amount"] or 0)
        b["models"][l["model"]] += 1
        b["keys"][l["api_key_masked"]] += 1
        b["cwd"] = l["cwd"] or b["cwd"]
        t = l["consume_time"]
        b["first"] = t if b["first"] is None else min(b["first"], t)
        b["last"] = t if b["last"] is None else max(b["last"], t)
    sessions = []
    for s, v in sorted(by_sess.items(), key=lambda kv: -kv[1]["amount"]):
        sessions.append({
            "session": s, "cwd": v["cwd"], "calls": v["calls"], "amount": round(v["amount"], 4),
            "models": v["models"].most_common(4), "keys": v["keys"].most_common(3),
            "first": datetime.datetime.fromtimestamp(v["first"] + DISPLAY_TZ, datetime.UTC).strftime("%Y-%m-%d %H:%M") if v["first"] else None,
            "last": datetime.datetime.fromtimestamp(v["last"] + DISPLAY_TZ, datetime.UTC).strftime("%Y-%m-%d %H:%M") if v["last"] else None,
        })

    # --- 按工作目录（项目）聚合 ---
    by_cwd = defaultdict(lambda: {"calls": 0, "amount": 0.0, "sessions": set()})
    for l in links:
        c = l["cwd"] or "(未知目录)"
        by_cwd[c]["calls"] += 1
        by_cwd[c]["amount"] += float(l["amount"] or 0)
        if l["session"]:
            by_cwd[c]["sessions"].add(l["session"])
    cwds = [{"cwd": c, "calls": v["calls"], "amount": round(v["amount"], 4), "sessions": len(v["sessions"])}
            for c, v in sorted(by_cwd.items(), key=lambda kv: -kv[1]["amount"])]

    matched_amount = sum(float(l["amount"] or 0) for l in links)
    total_amount = sum(float(r.get("amount") or 0) for r in pt)
    out = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stat": stat,
        "totals": {
            "total_amount": round(total_amount, 4),
            "matched_amount": round(matched_amount, 4),
            "unmatched_amount": round(total_amount - matched_amount, 4),
        },
        "keys": keys,
        "sessions": sessions,
        "cwd": cwds,
        "links": links[:5000],
    }
    print("[3/3] 写出 -> %s" % os.path.abspath(args.out))
    print("      已归因 $%.4f / 未归因 $%.4f" % (out["totals"]["matched_amount"], out["totals"]["unmatched_amount"]))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)


if __name__ == "__main__":
    main()
