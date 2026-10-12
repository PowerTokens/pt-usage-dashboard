# PowerTokens 用量看板（pt-usage-dashboard） / PowerTokens Usage Dashboard

把你（或你的团队）在 [PowerTokens](https://www.powertokens.ai) 上调过的模型消耗，生成一张**单文件离线 HTML 看板**——KPI、按模型/按天/按模态成本趋势、调用明细全都有，**零外部依赖、可邮件转发、可进汇报**。
Generate a **single-file offline HTML dashboard** of your (or your team's) model spend on [PowerTokens](https://www.powertokens.ai) — KPIs, cost trends by model/day/modality, and call details — **zero external dependencies, email-friendly, presentation-ready**.

数据来源是 PT **网页控制台的私有接口**（逐笔精确 amount / quota / token），不靠估算。只要你能登录 PT 网页、复制一个 Cookie，就能拉到你账号的全部历史消耗。
Data comes from PT's **private console API** (per-call exact amount / quota / token), not estimates. As long as you can log into PT and copy one Cookie, you can pull your entire account history.

---

## 这个仓库有两种用法 / Two ways to use this repo

| 用法 / Usage | 适合谁 / For | 怎么用 / How |
|---|---|---|
| **① 当作 GitHub 上的独立 Python 工具** | 任何用 PT 的人（不限客户端） | 克隆仓库 → 装好 Python → 跑两条命令出 HTML |
| **① As a standalone Python tool on GitHub** | Anyone using PT (any client) | Clone → install Python → run two commands to get the HTML |
| **② 当作 WorkBuddy skill 安装** | WorkBuddy 用户 | 把本目录放进 `~/.workbuddy/skills/`，对话里说"看看我的 PT 用量"即可触发 |
| **② As a WorkBuddy skill** | WorkBuddy users | Drop this folder into `~/.workbuddy/skills/`, then ask "show my PT usage" in chat |

两种方式共用同一套 `scripts/*.py`，逻辑完全一致。下面以①为主说明，②在文末补充。
Both share the same `scripts/*.py` with identical logic. Section ① is the primary path; ② is covered at the end.

> **先说清楚本质 / The key point:** 本仓库就是一组**纯标准库 Python 脚本**，靠终端执行。任何能给你一个 Shell / 终端的 agent 或编辑器都能直接跑——Trae、Cursor、VS Code、dsh、Claude Code、Codex，甚至系统自带的 PowerShell / bash 都行，**和 WorkBuddy 没有绑定关系**。唯一和 WorkBuddy 强绑定的只有"调用↔对话归因"那一项（要读 WB 本地日志）。
> This repo is just a set of **pure standard-library Python scripts** run from a terminal. Any agent or editor that gives you a shell can run it — Trae, Cursor, VS Code, dsh, Claude Code, Codex, or even system PowerShell / bash — **no binding to WorkBuddy**. Only the "call↔conversation attribution" feature is WorkBuddy-specific (it reads WB local logs).

---

## 快速开始（3 步）/ Quick Start (3 steps)

### 1. 准备 Python / Prepare Python
只需 **Python 3.8+ 标准库**，无需 `pip install` 任何东西。/ Only **Python 3.8+ standard library** is needed — no `pip install`.
```bash
python3 --version   # 或 / or python --version
```

### 2. 拿到你的会话 token（关键）/ Get your session token (key step)
1. 浏览器登录 https://www.powertokens.ai ，进入「用量 / 消耗」页面。/ Log into https://www.powertokens.ai in your browser, open the "Usage / Spend" page.
2. 按 `F12` 打开开发者工具 → **Application（应用）** → **Cookies** → 位点 `https://www.powertokens.ai`。/ Press `F12` → **Application** → **Cookies** → site `https://www.powertokens.ai`.
3. 找到名为 **`_Bearer`** 的 Cookie，复制它的 **Value（一整串 JWT）**。/ Find the cookie named **`_Bearer`**, copy its **Value (the whole JWT)**.
4. 把仓库里的 `config.example.json` 复制为 `config.json`，把 token 填进去：/ Copy `config.example.json` to `config.json` and paste the token:
   ```json
   { "base_url": "https://www.powertokens.ai/api", "session_token": "这里粘贴_Bearer的值 / paste _Bearer value here" }
   ```
   > `config.json` 已被 `.gitignore` 忽略，**不会**被提交，请放心填。也支持环境变量 `PT_SESSION_TOKEN` 或 `--token` 参数传入。/ `config.json` is in `.gitignore` and **won't** be committed. You can also use env var `PT_SESSION_TOKEN` or the `--token` argument.

### 3. 拉数据 + 出看板 / Fetch data + build dashboard
```bash
python3 scripts/fetch_pt_server.py --since 2025-01-01
python3 scripts/gen_dashboard.py --out pt-usage.html
```
用浏览器打开生成的 `pt-usage.html` 即可。**全程没有网址、没有服务器在跑**，就是写了一个本地文件。/ Open the generated `pt-usage.html` in a browser. **No URL, no server running** — it's just a local file.

---

## 在其它 agent / 工具里怎么产生看板（通用方式）/ How to build it in other agents / tools (universal)

不管你在哪个 agent 里，步骤一模一样——**找到它的终端，cd 进仓库，跑两条命令**：/ No matter which agent you use, the steps are identical — **open its terminal, cd into the repo, run the two commands**:

| 工具 / Tool | 打开终端的方式 / How to open terminal | 跑完在哪看 HTML / Where to view |
|---|---|---|
| **Trae** | 底部 Terminal 面板（或 `Ctrl`+`` ` ``），也可命令面板搜 "Terminal" | 文件树里双击 `pt-usage.html`，或系统默认浏览器打开 |
| **Cursor / VS Code** | 菜单 Terminal → New Terminal（`Ctrl`+`` ` ``） | 同上 / same |
| **dsh** | 它的 Shell / 终端 | 同上 / same |
| **Claude Code / Codex / 任何 CLI agent** | 直接在会话里执行 shell 命令 | 同目录的 `pt-usage.html` |
| **纯终端（PowerShell / bash / zsh）** | 直接开 | 直接打开文件 |

具体三步（以 Trae / Cursor / VS Code 为例）/ Three steps (e.g. Trae / Cursor / VS Code):
1. 把仓库放到任意目录（克隆或下载解压），在终端里 `cd` 进去。/ Put the repo anywhere (clone or unzip), `cd` into it in the terminal.
2. 按上文「快速开始 §2」拿到 `_Bearer` 填进 `config.json`。/ Get `_Bearer` per "Quick Start §2" and put it in `config.json`.
3. 在终端执行 / Run in terminal:
   ```bash
   python3 scripts/fetch_pt_server.py --since 2025-01-01
   python3 scripts/gen_dashboard.py --out pt-usage.html
   ```
   生成的 `pt-usage.html` 是本地文件，双击即可在浏览器看。**不需要 WorkBuddy、不需要联网、不需要任何服务在跑。** / The generated `pt-usage.html` is a local file — double-click to view in a browser. **No WorkBuddy, no network, no service required.**

---

## 界面语言（中 / 英，默认英文）/ UI Language (Chinese / English, default English)

- **默认英文**。看板右上角有 `中文 / EN` 切换按钮，点一下即切换；选择记在浏览器 `localStorage`，下次打开保持。/ **English by default.** A `中文 / EN` toggle sits at the top-right; click to switch. The choice is saved in the browser `localStorage` and persists.
- 也可用命令行在生成时固定默认语言 / You can also fix the default language at build time:
  ```bash
  python3 scripts/gen_dashboard.py --lang en    # 英文（默认）/ English (default)
  python3 scripts/gen_dashboard.py --lang zh    # 中文 / Chinese
  ```
  该参数只影响"首次打开时"的默认语言；页面内按钮仍可随时切换。/ This only sets the **initial** language; the in-page button can still switch anytime.

---

## 多账户（你有多个 PT 登录账号时）/ Multi-account (when you have several PT logins)

一份 `_Bearer` = 一个 PT 登录账号。如果你有 2 个以上能登录的账号，在 `config.json` 用 `accounts` 列表：/ One `_Bearer` = one PT login. If you have 2+ logins, use the `accounts` list in `config.json`:

```json
{
  "base_url": "https://www.powertokens.ai/api",
  "accounts": [
    { "name": "工作号 / Work",  "session_token": "<账号A的_Bearer / account A _Bearer>" },
    { "name": "个人号 / Personal",  "session_token": "<账号B的_Bearer / account B _Bearer>" }
  ]
}
```

`fetch_pt_server.py` 会**逐个账号拉取**（某个 token 过期只跳过并警告，不影响其它），各自缓存为 `pt_usage_server_工作号.json` / `pt_usage_server_个人号.json`。`gen_dashboard.py` 合并所有缓存，看板顶部出现**「账户」筛选条**，可一键只看某个账号。/ `fetch_pt_server.py` pulls **each account** (a single expired token is skipped with a warning, others are unaffected), caching to `pt_usage_server_<name>.json`. `gen_dashboard.py` merges all caches and shows an **Account filter bar** at the top.

---

## 可选增强（进阶）/ Optional enhancements (advanced)

| 功能 / Feature | 脚本 / Script | 说明 / Notes |
|---|---|---|
| 调用 ↔ 对话归因 + 按 API Key 拆分 | `join_conversations.py` | 按 key 全量精确汇总；把调用对回 WorkBuddy 本地会话（**仅 WorkBuddy 环境有意义**，命中率通常 10~20%，其余归入"其它客户端"） |
| Call ↔ conversation attribution + split by API Key | `join_conversations.py` | Exact per-key totals; maps calls back to WorkBuddy local sessions (**WorkBuddy-only**; hit rate ~10–20%, rest fall under "other clients") |
| 模型目录对照表 | `fetch_catalog.py` | 拉 `GET /v1/models` 做 PT 当前可用模型对照，**需要另一种凭据 `PT_API_KEY`**（见下方"两种 token 的区别"） |
| Model catalog | `fetch_catalog.py` | Pulls `GET /v1/models` for a model list — **needs a different credential `PT_API_KEY`** (see "Two tokens" below) |
| 本地手动记录 | `pt_log.py` | 没有服务端 token 时，每次调 PT 后手动记一笔 / 解析报错里的 `need quota` 取真实费用 |
| Local manual log | `pt_log.py` | When no server token, manually log each PT call / parse `need quota` from errors for the real cost |

---

## ⚠️ 三个绕不过的坑（已写进脚本，了解即可）/ Three unavoidable pitfalls (handled in code)

1. **`Authorization` 头不能加 `Bearer ` 前缀。** PT 网页前端是把 Cookie 值直接赋给 `Authorization`，所以我们的请求头也是**原文** `Authorization: <token>`。加了 `Bearer ` 会返回 `401 Login authorization invalid`。/ **No `Bearer ` prefix** on the `Authorization` header — PT's frontend assigns the Cookie value directly, so we send the raw `Authorization: <token>`. Adding `Bearer ` returns `401`.
2. **服务端每页硬上限 100 条。** 传 `page_size=200` 也只返 100。脚本按"实际返回条数"累加计账，并采用"按月粗筛 → 对活跃月份按天翻页"规避深翻页上限。/ **Server hard-limits 100 rows per page.** The script counts by actual rows returned and uses month→day paging to avoid the deep-paging cap.
3. **`_Bearer` 随登录会话过期（实测约 3 天）。** 过期后重新复制一次 Cookie 即可。这是 PT 网页鉴权机制决定的，工具侧无法绕开。/ **`_Bearer` expires with the session (~3 days in practice).** Re-copy the Cookie after expiry — this is PT's auth mechanism, not something the tool can bypass.

---

## 两种 token 的区别（容易混）/ Two different tokens (easy to confuse)

| token | 是什么 / What | 用在哪 / Used for | 前缀 / Prefix |
|---|---|---|---|
| **`_Bearer` 会话 Cookie** | 浏览器登录 PT 后产生的会话凭证 | `fetch_pt_server.py` 拉**用量/账单**（本看板主路） | 原文，**不加 Bearer** |
| **`_Bearer` session cookie** | Session credential after logging into PT in browser | `fetch_pt_server.py` pulls **usage/billing** (main path) | raw, **no Bearer** |
| **`PT_API_KEY`** | PT 公开 API 的接入 key（带 `Bearer`） | `fetch_catalog.py` 拉**模型目录**（`api.powertokens.ai/v1`） | 需要 `Bearer ` 前缀 |
| **`PT_API_KEY`** | Access key for PT's public API (with `Bearer`) | `fetch_catalog.py` pulls **model catalog** (`api.powertokens.ai/v1`) | needs `Bearer ` prefix |

两者**不是一回事**：只有登录会话能查用量账单，API key 查不到自己的消耗历史（公开 API 压根没有用量端点，我们已探测确认）。/ The two are **not interchangeable**: only a login session can query usage/billing; the API key cannot retrieve your spend history (the public API has no usage endpoint — we verified this).

---

## 已知限制（诚实说明）/ Known limitations (honest notes)

- **只给 key、登不进账号的"分配 key"无法自动统计。** 公开 API 没有"凭 key 查用量"的接口，逐笔用量只在登录会话接口里。这类账号的消耗：让分配 key 给你的**账号主人**用同款工具导出看板发你；或你拿到该账号登录后复制 `_Bearer` 走多账户方案；或用 `pt_log.py` **本地手动记**作为兜底（非全量历史）。/ **Keys you were given but can't log into can't be auto-tracked.** The public API has no "usage by key" endpoint; per-call usage lives only behind a login session. Options: ask the key's owner to export a dashboard for you; get the login and use multi-account; or use `pt_log.py` to log manually (not full history).
- 看板是生成那一刻的**静态快照**。要看更新的消耗，重新跑一次 `fetch` + `gen` 即可（HTML 不会被 live 刷新）。/ The dashboard is a **static snapshot** at build time. To see newer spend, re-run `fetch` + `gen` (the HTML does not live-refresh).
- 会话归因（调用对回哪个聊天）**仅限 WorkBuddy 本地日志**，跨客户端（dsh / Trae / pt CLI）的调用归不到具体会话，会显示在"其它客户端（未归因）"。/ Session attribution is **WorkBuddy-local-log only**; cross-client calls (dsh / Trae / pt CLI) can't map to a specific chat and show under "other clients (unattributed)".

---

## 看板包含什么 / What the dashboard includes

- **KPI**：所选时间范围的消耗（服务端精确）/ 调用次数 / 总 Token / 输入:输出比 / 活跃天数 / 账户余额
- **KPIs**: spend in selected range (server-exact) / calls / total tokens / input:output ratio / active days / account balance
- **时间筛选条**：近 24h / 3天 / 7天 / 30天 / 90天 / 全部，一键联动所有图表
- **Time filter bar**: 24h / 3d / 7d / 30d / 90d / All, linked to every chart
- **成本趋势**（按天折线）、**日活分布**（小时×日期热力图）、**单次请求 token 大小分布**（按模型山脊图）、**0-24 时按模型分布**、**每日模型用量堆叠柱**
- **Cost trend** (daily line), **daily activity** (hour×date heatmap), **token size distribution** (per-model ridge), **0–24h by model**, **daily model usage stacked bars**
- **按模型成本**、**按模态占比**（视频/图像/音频/对话）、**按 API Key 成本**（全量精确）
- **Cost by model**, **by modality** (video/image/audio/chat), **by API Key** (full precision)
- **调用 ↔ 对话归因**、**调用明细**（最近 200 条）
- **Call ↔ conversation attribution**, **call details** (latest 200)
- 深浅色主题切换（记忆偏好），以及右上角 **中文 / EN 语言切换**
- Light/dark theme toggle (remembered) plus the top-right **中文 / EN language switch**

---

## ② 可选：当 WorkBuddy skill 安装 / ② Optional: install as a WorkBuddy skill

上面①是通用主路径，谁都能用。如果你刚好是 WorkBuddy 用户，也可以把它当 skill 用：把本目录整体放到 `~/.workbuddy/skills/pt-usage-dashboard/`，重启 WorkBuddy，之后在对话里说"看看我的 PT 用量""PT 模型消耗看板"即可触发（skill 的 `SKILL.md` 里有完整数据源优先级与执行步骤）。**装不装成 skill 不影响功能**，只是多了"用自然语言触发"这一层便利。/ Section ① is the universal path anyone can use. If you happen to be a WorkBuddy user, you can also use it as a skill: drop this folder into `~/.workbuddy/skills/pt-usage-dashboard/`, restart WorkBuddy, then say "show my PT usage" in chat (full steps in `SKILL.md`). **Installing as a skill changes nothing functionally** — it only adds natural-language triggering.

---

## License

[MIT](./LICENSE) © 2026 tjj1234
