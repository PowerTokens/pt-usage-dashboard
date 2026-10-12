# -*- coding: utf-8 -*-
"""PowerTokens 调用记录器（pt-usage-dashboard 的数据源 B）

每次在 dsh / pt CLI / 自建脚本里调完 PT，记一笔：模型 + 参数 + 状态。
也可把 PT 的余额不足报错贴给 --parse，抓出真实费用（need quota: $0.200000）。
记录写入 <skill>/pt_calls.jsonl，看板 gen_dashboard.py 读取。

用法：
  python pt_log.py --model wan3.0-video --seconds 5 --resolution 720P --status success
  python pt_log.py --model wan3.0-video --seconds 5 --parse "token quota is not enough, need quota: $0.200000"
  python pt_log.py --list            # 查看已记录条数
  python pt_log.py --calls 自定义.jsonl --model ...   # 自定义日志路径
"""
import argparse, json, os, sys, re, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MODE_KEYWORDS = {
    "video": ["seedance", "wan", "kling", "dreamina", "hunyuan-video", "minimax", "video", "step"],
    "image": ["flux", "kolors", "cogview", "seedream", "image", "sd3", "stable-diffusion"],
    "audio": ["cosyvoice", "seed-tts", "music", "audio", "tts", "speech"],
    "chat":  ["deepseek", "glm", "qwen", "doubao", "gpt", "claude", "chat", "abab", "moonshot"],
}


def classify(model):
    m = (model or "").lower()
    for mod, kws in MODE_KEYWORDS.items():
        if any(k in m for k in kws):
            return mod
    return "other"


# 价格表（USD @5s/720p），与 gen_dashboard.py 同步；仅为估算
PRICE = {
    "wan3.0-video": 0.20, "wan3.0-video-prime": 0.70, "wan2.7-t2v": 0.475,
    "seedance-1-5-pro-251215": 0.247, "dreamina-seedance-2-0-fast-260128": 0.605,
    "dreamina-seedance-2-0-260128": 0.756, "kling-video-o1": 0.42, "kling-v3": 0.42,
    "kling-v2-1-master": 1.40, "hunyuan-video": 0.30,
    "flux": 0.04, "kolors": 0.03, "cogview": 0.03, "seedream": 0.05,
    "cosyvoice": 0.01, "seed-tts": 0.01, "music": 0.05,
}
RES_FACTOR = {"480P": 0.5, "720P": 1.0, "1080P": 2.0, "default": 1.0}


def estimate(model, seconds, resolution):
    base = PRICE.get(model)
    if base is None:
        for k, v in PRICE.items():
            if model and model.startswith(k.split("-")[0]) and v > 0:
                base = v; break
    if base is None or base == 0:
        return None
    secs = float(seconds) if seconds else 5.0
    res = (resolution or "720P").upper()
    f = RES_FACTOR.get(res, RES_FACTOR["default"])
    mod = classify(model)
    if mod in ("video", "image"):
        return round(base * (secs / 5.0) * f, 4)
    return round(base, 4)


def parse_quota(text):
    """从 PT 报错里抓本次真实预扣额（need quota），兼容 $ 与全角 ＄。

    PT 报错形如：token remain quota: $0.148000, need quota: $0.200000
    其中 need quota 才是本次调用的预扣成本，remain quota 是剩余额度。
    """
    if not text:
        return None
    # 优先 need quota（本次成本）
    m = re.search(r"need\s+quota[:\s]*[＄$]\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if m:
        return float(m.group(1))
    # 退而求其次 remain quota
    m = re.search(r"remain\s+quota[:\s]*[＄$]\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if m:
        return float(m.group(1))
    # 兜底：第一个 $ 数字
    m = re.search(r"[＄$]\s*([0-9]+(?:\.[0-9]+)?)", text)
    return float(m.group(1)) if m else None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="PowerTokens 调用记录器")
    ap.add_argument("--model", help="模型名（如 wan3.0-video）")
    ap.add_argument("--seconds", type=float, help="时长（秒）")
    ap.add_argument("--resolution", help="分辨率 480P/720P/1080P")
    ap.add_argument("--status", default="success", choices=["success", "failed", "unknown"])
    ap.add_argument("--parse", help="贴入 PT 报错文本，自动抓真实费用")
    ap.add_argument("--note", default="", help="备注")
    ap.add_argument("--calls", default=os.path.join(skill_dir, "pt_calls.jsonl"), help="日志路径")
    ap.add_argument("--list", action="store_true", help="查看已记录条数")
    args = ap.parse_args()

    if args.list:
        n = 0
        if os.path.exists(args.calls):
            with open(args.calls, encoding="utf-8", errors="ignore") as fh:
                n = sum(1 for _ in fh if _.strip())
        print(f"[pt_log] {args.calls} 已记录 {n} 条")
        return

    if not args.model:
        print("[pt_log] 需要 --model；或 --list 查看记录。")
        return

    real = parse_quota(args.parse) if args.parse else None
    est = estimate(args.model, args.seconds, args.resolution)
    rec = {
        "ts": int(datetime.datetime.now().timestamp()),
        "model": args.model,
        "modality": classify(args.model),
        "seconds": args.seconds,
        "resolution": args.resolution,
        "status": "failed" if real is not None and args.status == "success" else args.status,
        "cost_est": est,
        "cost_real": real,
        "note": args.note or (("parse: " + args.parse) if args.parse else ""),
        "source": "logger",
    }
    with open(args.calls, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[pt_log] 已记录: {args.model} {args.seconds or '-'}s {args.resolution or '-'} "
          f"est={est} real={real} -> {args.calls}")


if __name__ == "__main__":
    main()
