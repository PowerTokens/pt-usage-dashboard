---
name: pt-usage-dashboard
description: 生成「我在 PowerTokens（powertokens.ai）上调过的模型」的消耗看板（单文件离线 HTML，含 KPI / 按模型·按天·按模态的成本趋势 / 调用明细）。当用户想查看自己在 PT 上的模型用量、消耗统计、成本审计时使用，例如"看看我的 PT 用量""PT 模型消耗看板""我在 PowerTokens 上花了多少""我的 PT 调用记录"。
---

# PowerTokens 用量看板

为本机生成一张 **PT（PowerTokens）模型调用消耗看板**（单文件自包含 HTML，离线可看，无外部依赖）。

> **⚠ 重要更正（2026-10-10 实测）**：早期结论「PT 没有远程用量 / 账单 API」**已被推翻**。
> PT 的 Console API（`/v1/*` 之下确实没有）之外，**网页控制台有自己的私有 REST 接口**，
> 能返回逐笔精确消耗、账户余额、官方聚合。也就是说：
> **看板可以不靠估算，直接用服务端权威数据。** 下面是数据优先级：

| 优先级 | 数据源 | 精确性 | 覆盖范围 |
|---|---|---|---|
| **① 权威** | **PT 控制台 API**（`fetch_pt_server.py` → `pt_usage_server.json`） | **精确**（每笔 amount / quota / token） | 账户在 PT 上的**全部**历史调用（所有 key、所有渠道） |
| ② 补充 | 本地记录器 `pt_log.py`（`pt_calls.jsonl`） | 估算/报错取值 | dsh / pt CLI / 自建脚本等途径 |
| ③ 补充 | WorkBuddy 日志 `~/.workbuddy/projects/**/*.jsonl` | 估算 | 经 WorkBuddy PT provider 的调用 |

有 ① 时，**以 ① 为准**，②③ 只是兜底；`gen_dashboard.py` 会自动合并。

---

## ① 服务端真实数据（首选，需浏览器会话 token）

### 鉴权方式（最大的坑）

- 令牌来源：浏览器登录 PT 后，**Cookie `https://www.powertokens.ai` → `_Bearer`**（非 httpOnly，DevTools → Application → Cookies 可读）。
- 请求头写法：`Authorization: <token 原始值>` —— **不要加 `Bearer ` 前缀**！加了会返回 `401 Login authorization invalid`。
  （原因：前端 axios 拦截器是 `headers.Authorization = cookie值` 直接赋值。）
- 把 token 写入本目录 `config.json` 的 `session_token` 字段（文件 UTF-8 无 BOM），或设环境变量 `PT_SESSION_TOKEN`，或 `--token` 传入。
- token 随登录会话过期（实测有效期约 3 天），过期重新复制一次即可。

### 可用端点（均为 `POST https://www.powertokens.ai/api/...`）

| 端点 | 用途 |
|---|---|
| `/usage-records/list` | **逐笔消耗**：`consume_time / consume_id / request_id / model_id / amount / quota / usage / request_params / status / api_key_masked` |
| `/usage-records/daily-quota` | 按天汇总 buckets |
| `/bill/balance` | 余额：`credits / equal_usd / auto_pay_enabled` |
| `/dashboard/usage` | 官方聚合：`spend_usd / requests / success_rate` + 按 key 排行 |
| `/dashboard/model-consume-rank` | 官方按模型花费排行（带展示名） |

必填参数：`start_timestamp`、`end_timestamp`（**unix 秒**）、`page`（最小为 1）。

### 第二个坑：每页硬上限 100

服务端把每页条数限制为 **100**（传 `page_size=200` 也只返 100）。
**计账必须按实际返回条数累加**，不能按请求的 `page_size` 折算 —— 否则会在翻到一半时误判「已拉完」而丢数据。
`fetch_pt_server.py` 已按此实现，并采用「按月粗筛 → 只对有量月份/天翻页」规避深翻页。

### 运行

```
python <skill目录>/scripts/fetch_pt_server.py --since 2026-01-01
python <skill目录>/scripts/gen_dashboard.py --out <工作区>/pt-usage.html
```

校验方法：`gen_dashboard.py` 报出的「真实合计」应与 `/dashboard/usage` 的 `spend_usd` 一致
（实测：5738 笔 → $54.9210 vs 官方 $54.92 ✓）。

---

## ②③ 本地兜底数据源（无 token 时仍可用）

1. **WorkBuddy 现有日志**：`~/.workbuddy/projects/**/*.jsonl` 中 `providerData.model` 命中 PT 模型特征（seedance / wan / kling / dreamina / minimax / hunyuan-video / flux / kolors / cosyvoice 等，或 `provider` 为 `powertokens`/`pt`）的记录。成本按 PRICE 表估算。
2. **skill 自带调用记录器 `pt_log.py`**：在 dsh / pt CLI / 自建脚本里调 PT 后记一笔，或把 PT 报错贴给它解析真实费用（`need quota: $0.200000`）。写入 `pt_calls.jsonl`。

## 执行步骤

1. **确认 Python**：脚本仅依赖 Python 3.8+ 标准库（**无需安装任何依赖**），用系统 `python3` / `python` 运行即可（WorkBuddy 环境会优先用托管 Python）。
2. **优先拉服务端数据**（有 `_Bearer` token时）：

   ```
   python <skill目录>/scripts/fetch_pt_server.py --since 2026-01-01
   ```

   token 过期时的引导：`code:401 Login authorization invalid` → 让用户浏览器打开 PT → F12 → Application → Cookies → `https://www.powertokens.ai` → 复制 `_Bearer` → 回贴或写入 `<skill目录>/config.json` 的 `session_token`。
3. **出看板**：

   ```
   python <skill目录>/scripts/gen_dashboard.py --out <工作区>/pt-usage.html
   ```

   - **界面语言**：默认英文，加 `--lang en|zh` 设定首次打开的默认语言（如 `--lang zh` 出中文版）；看板右上角另有 `中文 / EN` 按钮可随时切换，选择记在浏览器 `localStorage`。/ UI language: English by default; `--lang en|zh` sets the initial language; an in-page `中文 / EN` toggle switches anytime (saved in localStorage).
   - 自动合并服务端（权威）+ 本地兜底数据；服务端缺失也能出图，只是标注为空。
   - 服务端更新时间显示在看板「PT 控制台官方统计」卡片里；数据陈旧时先跑第 2 步。
3. **没有数据时的引导**：告诉用户「先在调用 PT 时记一笔」：

   ```
   # 记一次成功调用（自动按 PRICE 表估费）
   python <skill目录>/scripts/pt_log.py --model wan3.0-video --seconds 5 --resolution 720P --status success

   # 或把 PT 的余额不足报错贴给它，抓真实费用
   python <skill目录>/scripts/pt_log.py --model wan3.0-video --seconds 5 --parse "token quota is not enough, need quota: $0.200000"
   ```

   （`pt_log.py` 也接受 `--calls` 指定自定义日志路径，`--note` 加备注。）
4. **（可选）拉模型目录做对照**：若想在看板里加「PT 当前可用模型 / 你的 key 有额度的模型 / 定价」对照表，先跑：

   ```
   python <skill目录>/scripts/fetch_catalog.py          # 需要 PT_API_KEY（env 或本目录 config.json）
   ```

   生成 `pt_models.json`，下次 `gen_dashboard.py` 会自动并入。设置 key：`fetch_catalog.py --set-key <KEY>`。
5. **（可选但推荐）把调用对回对话 + 按 API key 拆分**：

   ```
   python <skill目录>/scripts/join_conversations.py
   ```

   产出 `pt_conversation_map.json`：按 API key 汇总（**全量精确**）+ 按会话 / 工作目录归因 + 匹配统计。

   **匹配原理（重要口径）**：PT 与本机会话**没有共享 request_id**（本地是 32 位 UUID，PT 是 `20261010…RhP5ELjW`），
   所以用「模型名（本地去掉 `custom-local:` 前缀）+ 时间戳就近 ±120s、一对一贪心占用」做**指纹匹配**。
   实测命中的 |Δt| 中位数 ≈0.8s，属强命中。

   ⚠ **匹配率通常只有 10~20% 是正常的**：dsh / Trae / pt CLI / 自建脚本的调用不进 WorkBuddy 日志，
   这部分在看板里归入「其它客户端（未归因）」单独展示，**汇报时必须说明，不要把这些算到会话头上**。
6. **展示结果**：用文件展示工具打开发出的 HTML，并口头汇报关键数字：服务端精确消耗总额、账户余额、调用次数、TOP 模型、按 key 分布、对话归因情况。

## 看板能力

- **KPI**：随时间筛选联动——所选范围消耗（$）/ 调用次数 / 总 Token / 输入:输出比 / 活跃天数 / 账户余额
- **时间范围筛选**：近24h / 近3天 / 近7天 / 近30天 / 近90天 / 全部，一键联动以下所有分布图与 KPI
- **成本趋势**：按天的成本折线（估算部分 + 真实部分分开标，随筛选联动）
- **日活分布**：小时 × 日期热力图，附凌晨/上午/下午/晚间四段占比
- **单次请求 token 大小分布**：按模型的山脊式直方图（对数轴），标注 p50 中位数（实线）与 p90（虚线）
- **0-24 时分布 · 按模型**：24 小时堆叠面积图（Top 8 模型）
- **每日模型用量比例**：按天的调用次数堆叠柱（Top 8 模型 + 其他）
- **按模型成本**：各模型柱状图（可点高亮）
- **按模态占比**：视频 / 图像 / 音频 / 对话 环形图
- **按 API Key 成本**：每个 key 的调用数 / 金额 / quota / 主要模型 / 最近调用（全量精确）
- **调用 ↔ 对话 归因**：匹配率、已归因金额、其它客户端金额；Top 会话（时间 / 工作目录 / 会话号 / 调用数 / 金额 / 模型）
- **调用明细**：最近 N 条（时间 / 模型 / 参数 / 状态 / 预估 / 真实费用 / 来源）

> 分布类图表（日活/山脊/时段/堆叠柱）由前端从逐笔数据（`DATA.recs`：ts/model/tokens/成本）实时计算，全部支持时间筛选；逐笔 token 明细来自服务端 Source ①。
- 深浅色主题切换（记忆偏好）、1 / 7 / 30 天窗口 + 自定义区间

## 口径与注意

- **服务端数据（Source ①）是精确值**：`amount` 由 PT 给出（单位 USD，`quota` 是 credits，二者约为 1000:1）。
- **②③ 的成本是估算**（按脚本头部 `PRICE` 表：[5s/720p 基价, 时长线性, 1080p=720p×2, 480p≈×0.5]），汇报时应说明这部分是粗略预估。
- 时间统一按**北京时间 (+8)** 展示；PT 返回的时间戳本身是 UTC。
- 模型 ID 会漂移，目录对照表来自 `GET /v1/models`，不要硬编码。
- 会话 token 是登录凭据：只在本机使用，不要写进演讲稿/提交到 git（`<skill目录>/config.json` 建议保持本地私有）。
- 看板数据完全来自本机日志 / 你手动记录的调用，**不会外发、不会上传**；`fetch_catalog.py` 仅在显式运行时才访问 PT 网络。
- Windows 控制台为 GBK 时脚本已内置 UTF-8 输出保护，乱码不影响 HTML 生成。
- **改 `gen_dashboard.py` 的 JS 部分时注意转义坑**：HTML 模板写在 Python 三引号字符串里，
  JS 里的 `\\` 会被 Python 吃掉一层变成 `\`，导致 `.split("\")` 这类写法直接语法错误、
  **整个脚本不执行、看板全空白**。需要反斜杠时用 `String.fromCharCode(92)` 或把模板串改成原始字符串。
  改完务必验证（两步缺一不可，只查数据不查 JS 曾导致「数据对但整页空白」事故）：
  ① `node --check <抽出的 script>` 不报 SyntaxError，且 JS 引用的 DOM id 在 HTML 中都存在；
  ② **运行时冒烟**：把抽出的 script 拼上一个最小 DOM 桩（`getElementById` 返回带 `innerHTML`/`style`/`querySelectorAll` 的 stub 对象，`querySelectorAll` 按 innerHTML 里 `<rect|<path|<circle|<button` 出现次数返回 stub 数组）后在 node 里真跑一遍，确认不抛异常、各图表容器 innerHTML 非空；再设 `activeDays=30;renderAll()` 验证筛选路径。
