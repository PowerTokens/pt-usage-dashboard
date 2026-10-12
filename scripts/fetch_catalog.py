# -*- coding: utf-8 -*-
"""可选：拉取 PowerTokens 模型目录（GET /v1/models）保存为 pt_models.json，供看板做对照表。

需要 PT_API_KEY：环境变量 PT_API_KEY，或本目录 config.json（{"api_key":"..."}）。
用法：
  python fetch_catalog.py                 # 拉取并保存
  python fetch_catalog.py --set-key xxx   # 写入 config.json (0600)
"""
import argparse, json, os, sys, stat, urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.powertokens.ai/v1/models"


def get_key(skill_dir):
    if os.environ.get("PT_API_KEY"):
        return os.environ["PT_API_KEY"]
    cfg = os.path.join(skill_dir, "config.json")
    if os.path.exists(cfg):
        try:
            return json.load(open(cfg, encoding="utf-8")).get("api_key")
        except Exception:
            return None
    return None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="拉取 PT 模型目录")
    ap.add_argument("--set-key", help="写入 config.json")
    ap.add_argument("--out", default=os.path.join(skill_dir, "pt_models.json"))
    args = ap.parse_args()

    if args.set_key:
        cfg = os.path.join(skill_dir, "config.json")
        json.dump({"api_key": args.set_key}, open(cfg, "w", encoding="utf-8"))
        try:
            os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        print(f"[fetch_catalog] key 已写入 {cfg} (0600)")
        return

    key = get_key(skill_dir)
    if not key:
        print("[fetch_catalog] 未找到 PT_API_KEY（env 或 config.json）。先跑 --set-key，或设环境变量。")
        return

    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {key}", "User-Agent": "pt-usage-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[fetch_catalog] 请求失败: {e}")
        return

    models = data.get("data") or data.get("models") or data
    if isinstance(models, dict):
        models = models.get("data") or []
    out = {"fetched_at": __import__("datetime").datetime.now().isoformat(), "models": models}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[fetch_catalog] 已保存 {len(models) if isinstance(models, list) else '?'} 个模型 -> {args.out}")


if __name__ == "__main__":
    main()
