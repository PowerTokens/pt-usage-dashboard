# -*- coding: utf-8 -*-
"""从 PowerTokens 控制台拉取「真实消耗数据」（Source C：服务端权威数据）。

先看具体场景：你在 PT 网页明细里看到的每一笔 quota / amount，本机也能原样拿到 ——
本脚本用网页登录的会话 token，调 PT 控制台自家的私有接口，把逐笔消耗拉下来存成本地缓存 JSON。
之后 gen_dashboard.py 读这个缓存出图，看板就不再是「估算」，而是精确值。

鉴权：Authorization 头 = 会话 token 原文（**不加 Bearer 前缀**）。
      token 位于浏览器 Cookie `https://www.powertokens.ai` → `_Bearer`（随登录会话过期）。

主要端点（均为 POST /api/...）：
  /usage-records/list          逐笔消耗记录（必填 start_timestamp、end_timestamp、page）
  /bill/balance                账户余额 credits / equal_usd / 自动充值开关
  /dashboard/usage             官方聚合：总花费、请求数、成功率、按 key 排行
  /dashboard/model-consume-rank 官方按模型花费排行（带展示名）
  /usage-records/daily-quota   按天汇总 bucket

⚠ 服务端对翻页深度有限制（page*page_size 超过约 5000 后返回空）。
  故采用「按月粗筛 → 只对有量月份按天拉取 → 天内再翻页」的方式规避。

仅依赖标准库。
"""
import argparse, json, os, re, sys, time, datetime, urllib.request, urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_accounts(skill_dir, cli_token=None):
    """返回 [(name, token), ...]。三种配置都支持：
    - --token 直接传（单账户，name=default）
    - config.json 的 accounts 列表：[{name, session_token}, ...]
    - config.json 的 session_token（单账户，name=default）
    env PT_SESSION_TOKEN 作为最终兜底。
    """
    if cli_token:
        return [("default", cli_token)]
    cfg = os.path.join(skill_dir, "config.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8-sig") as fh:
                c = json.load(fh)
            accts = c.get("accounts")
            if isinstance(accts, list):
                out = []
                for a in accts:
                    if isinstance(a, dict) and a.get("session_token"):
                        out.append((a.get("name") or "account", a["session_token"]))
                if out:
                    return out
            tok = c.get("session_token")
            if tok:
                return [("default", tok)]
        except Exception:
            pass
    env = os.environ.get("PT_SESSION_TOKEN")
    if env:
        return [("default", env.strip())]
    return []


class PTConsole:
    def __init__(self, token, base_url="https://www.powertokens.ai/api"):
        self.token = token
        self.base = base_url
        self.last_error = None

    def post(self, path, body):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={
                "Authorization": self.token,          # 注意：原文，无 Bearer 前缀
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "X-App-Version": "2.0.3",
                "Locale": "en",
                "Zone-Offset": "8",
            })
        try:
            r = urllib.request.urlopen(req, timeout=60)
            return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return {"code": e.code, "msg": e.read()[:300].decode("utf-8", "replace"), "data": None}
        except Exception as e:
            return {"code": -1, "msg": "%s: %s" % (type(e).__name__, e), "data": None}


def month_bounds(since_ts, now_ts):
    """返回 [since, now] 之间的自然月窗口列表 [(start_ts, end_ts, 'YYYY-MM'), ...]"""
    out = []
    d = datetime.datetime.fromtimestamp(since_ts, datetime.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_dt = datetime.datetime.utcfromtimestamp(now_ts)
    while True:
        y, m = d.year, d.month
        nxt = datetime.datetime(y + (m == 12), (m % 12) + 1, 1)
        s = int(d.replace(tzinfo=datetime.timezone.utc).timestamp())
        e = min(int(nxt.replace(tzinfo=datetime.timezone.utc).timestamp()) - 1, now_ts)
        if s > now_ts:
            break
        out.append((s, e, d.strftime("%Y-%m")))
        d = nxt
        if len(out) > 400:
            break
    return out


def day_bounds(start_ts, end_ts):
    out = []
    d = datetime.datetime.fromtimestamp(start_ts, datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    while True:
        s = int(d.replace(tzinfo=datetime.timezone.utc).timestamp())
        e = s + 86399
        if s > end_ts:
            break
        out.append((s, min(e, end_ts), d.strftime("%Y-%m-%d")))
        d += datetime.timedelta(days=1)
        if len(out) > 500:
            break
    return out


def fetch_all_records(api, since_ts, now_ts, page_size=100, verbose=False):
    """按月粗筛 → 按天分页拉取全部逐笔记录。总数超过单月上限时自动降级到天粒度。"""
    records = {}
    months = month_bounds(since_ts, now_ts)
    active_months = []
    for s, e, label in months:
        r = api.post("/usage-records/list", {"start_timestamp": s, "end_timestamp": e, "page": 1, "page_size": 1})
        if r.get("code") != 200:
            if verbose:
                print("[warn] month %s -> %s %s" % (label, r.get("code"), str(r.get("msg"))[:120]))
            continue
        total = (r.get("data") or {}).get("total", 0)
        if total:
            active_months.append((s, e, label, total))
            if verbose:
                print("[month] %s total=%d" % (label, total))

    def pull_window(s, e, tag):
        """分页拉窗口内记录。返回 True=已拉完；False=疑似触到服务端深翻页上限。

        注意坑：服务端把每页条数硬上限设为 **100**（传 page_size=200 也只返 100），
        所以计账必须按「实际返回条数」累加，不能用请求时的 page_size 折算，否则会提前收工丢数据。
        """
        req_ps = 100
        page = 1
        got = 0
        while True:
            r = api.post("/usage-records/list",
                         {"start_timestamp": s, "end_timestamp": e, "page": page, "page_size": req_ps})
            if r.get("code") != 200:
                if verbose:
                    print("[warn] page fail %s p%d -> %s %s" % (tag, page, r.get("code"), str(r.get("msg"))[:120]))
                break
            d = r.get("data") or {}
            items = d.get("items") or []
            for it in items:
                cid = it.get("consume_id") or (it.get("request_id") or "") + str(it.get("consume_time"))
                records[cid] = it
            got += len(items)
            total = d.get("total", 0)
            if not items or got >= total:
                break
            if len(items) < req_ps:      # 实际返回不足一页异常，交给更细粒度兜底
                return False
            page += 1
            if page > 60:                # 单窗口超过约 6000 条 → 疑似触顶，降级到天粒度
                return False
        return True

    for s, e, label, total in active_months:
        ok = pull_window(s, e, label)
        if not ok:
            # 单月翻页触顶 → 拆到天粒度
            for ds, de, dl in day_bounds(s, e):
                ok2 = pull_window(ds, de, dl)
                if not ok2 and verbose:
                    print("[warn] day %s still hit cap, some records may be missing" % dl)
    recs = sorted(records.values(), key=lambda x: x.get("consume_time") or 0)
    return recs


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="拉取 PowerTokens 控制台真实消耗数据")
    ap.add_argument("--token", default=None, help="会话 token（也可放 config.json 的 session_token）")
    ap.add_argument("--since", default="2025-01-01", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--out", default=os.path.join(skill_dir, "pt_usage_server.json"), help="缓存输出路径")
    ap.add_argument("--base-url", default="https://www.powertokens.ai/api")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    accounts = load_accounts(skill_dir, args.token)
    if not accounts:
        sys.stderr.write("拿不到会话 token。请在浏览器中复制 Cookie `_Bearer` 的值，"
                         "写入 %s 的 session_token（或 accounts 列表），或用 --token 传入。\n"
                         % os.path.join(skill_dir, "config.json"))
        sys.exit(2)

    now = int(time.time())
    since = int(datetime.datetime.strptime(args.since, "%Y-%m-%d")
                .replace(tzinfo=datetime.timezone.utc).timestamp())

    total_all = 0.0
    for (acc_name, acc_token) in accounts:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", acc_name)
        # 单账户 / 默认账户 → 仍用用户指定的 --out（向后兼容 pt_usage_server.json）；
        # 多账户 → 各写 pt_usage_server_<名>.json，避免互相覆盖。
        if acc_name == "default" or len(accounts) == 1:
            out_path = args.out
        else:
            base = os.path.splitext(args.out)[0]
            out_path = "%s_%s.json" % (base, safe)
        print("==== 账户 [%s] -> %s ====" % (acc_name, os.path.basename(out_path)))

        api = PTConsole(acc_token, args.base_url)

        print("[1/3] 拉取逐笔消耗记录 ...")
        recs = fetch_all_records(api, since, now, verbose=args.verbose)
        print("      记录数: %d" % len(recs))

        print("[2/3] 拉取余额 ...")
        bal = api.post("/bill/balance", {})
        balance = bal.get("data") if bal.get("code") == 200 else None
        if bal.get("code") != 200:
            print("      balance 失败: %s %s" % (bal.get("code"), str(bal.get("msg"))[:120]))
            if bal.get("code") == 401:
                print("      ⚠ 该账户 token 已过期（401），跳过。请重新复制浏览器 Cookie `_Bearer`。")
                continue

        print("[3/3] 拉取官方聚合 ...")
        usage = api.post("/dashboard/usage", {"start_timestamp": since, "end_timestamp": now})
        rank = api.post("/dashboard/model-consume-rank", {"start_timestamp": since, "end_timestamp": now})
        daily = api.post("/usage-records/daily-quota", {"start_timestamp": since, "end_timestamp": now})

        out = {
            "account": acc_name,
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "since": args.since,
            "range": {"start_timestamp": since, "end_timestamp": now},
            "balance": balance,
            "dashboard_usage": usage.get("data") if usage.get("code") == 200 else None,
            "model_rank": rank.get("data") if rank.get("code") == 200 else None,
            "daily_quota": (daily.get("data") or {}).get("buckets") if daily.get("code") == 200 else None,
            "records": recs,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        print("已写出 -> %s" % os.path.abspath(out_path))
        if recs:
            tot = sum(r.get("amount") or 0 for r in recs)
            total_all += tot
            print("    区间 %s ~ 现在 | 笔数 %d | 合计 $%.4f" % (
                datetime.datetime.fromtimestamp(recs[0]["consume_time"], datetime.UTC).strftime("%Y-%m-%d"),
                len(recs), tot))
        if balance:
            print("    余额 credits=%s  ≈ $%s" % (balance.get("credits"), balance.get("equal_usd")))
    if len(accounts) > 1:
        print("全部账户合计 $%.4f" % total_all)


if __name__ == "__main__":
    main()
