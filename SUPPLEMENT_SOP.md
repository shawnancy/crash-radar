# crash-radar 档案候选补录 SOP

> 触发源: `~/bin/crash_radar_monitor.py`(launchd `com.shaw.crash-radar`, 每30min跑)新增的"档案候选事件扫描"——
> WATCHLIST(指数≥4% / 个股≥10% / 加密24h≥10%)命中后, iMessage 推大王手机 + 写入
> `~/.openclaw/workspace/sanliu/crash_radar/pending_events.json`。任意格子看到 iMessage 或大王转达"去补录",
> 照本文件走完全流程。**每步给确切命令+预期输出**, 别跳步, 别只做一半就报完成。

## 0. 先读

- 项目全貌: `~/.claude/projects/-Users-shaw/memory/kol/project_crash_radar_20260716.md`(含历史踩坑 E1-E8)
- 本地源码: `~/Desktop/skills/crash-radar/`(template.html + data/*.json + build.py → index.html)
- **改内容改 `data/` 或 `template.html`, 跑 `build.py` 再部署, 绝不直接改 `index.html`**(它是构建产物)
- 中英双构建: `build.py` 一次跑出 `index.html`(中文) + `en/index.html`(英文)。英文版事件数据来自 `data/en/events_us.json` / `data/en/events_crypto.json`, **必须与中文版逐条同序对齐**(同 id、同下标), 因为标签打标只在中文版算一次, 英文版按下标复制标签, 不重新打标。

## 1. 取候选清单

```bash
python3 -c "
import json
p = json.load(open('/Users/shaw/.openclaw/workspace/sanliu/crash_radar/pending_events.json'))
todo = [e for e in p if e['status'] == 'pending']
print(len(todo), 'pending')
for e in todo: print(e)
"
```
每条结构: `{"symbol","date","chg_pct","price","detected_at","status","group"}`(group=指数/个股/加密)。
没有 pending 条目就停在这里, 不用往下走。

## 2. 派 sonnet 调研前兆信号

对每条 pending 事件, 派一个 **sonnet** agent(机械调研活, 别用 opus)调研:
- 该标的当日/24h ±10%(或指数±4%)波动的**事发原因**(cause, 1段综述)
- **2-5 条前兆信号**(precursors), 每条必须是:
  ```json
  {"indicator": "指标名(如 财报指引/未平仓量/融资余额)",
   "signal": "具体数值+口径描述",
   "lead_time": "提前量, 如 提前约2周",
   "source_url": "信源URL, 不能是记忆里编的"}
  ```
- **aftermath**(后续走势1段) + **refs**(信源URL列表, 与 precursors 里的 source_url 汇总去重)
- 结构逐 key 对照 `data/events_us.json` 里任意一条现有条目(见下方字段表), 不要自创字段名

字段表(events_us.json / events_crypto.json 通用):
```
id market date asset move_pct title cause precursors[] aftermath refs[]
```
- `id`: `{date}-{英文slug}`, 如 `2026-07-24-tsla-earnings-miss`
- `market`: 美股大盘用 `us_index`, 美股个股用 `us_stock`, 加密用 `crypto`
- **数字必须对照权威信源核实, 禁止凭记忆或估算**(反图谋 D3/AS4 铁律) —— agent 调研结论里每个关键数字都要能对回 source_url

## 3. 追加中文档案 + 同步翻译英文档案

1. 中文条目 append 进 `data/events_us.json`(美股)或 `data/events_crypto.json`(加密), 用 Python 读→append→写回, 不要手改大 JSON 文件破坏格式:
   ```python
   import json
   f = "data/events_us.json"  # 或 events_crypto.json
   d = json.load(open(f))
   d.append(new_event)   # new_event = 上一步 agent 产出的完整字典
   json.dump(d, open(f, "w"), ensure_ascii=False, indent=1)
   ```
2. **同一事件**翻译成英文(可再派 sonnet, 或人工口译), 结构逐 key 一致, append 进
   `data/en/events_us.json` 或 `data/en/events_crypto.json` 的**同一位置**(append 到末尾即可, 只要两份文件事件总数和顺序保持一一对应)。
3. 回读校验两份文件事件数同步 +1, 且中英文 id 一致:
   ```bash
   python3 -c "
   import json
   zh = json.load(open('data/events_us.json'))
   en = json.load(open('data/en/events_us.json'))
   assert len(zh) == len(en), (len(zh), len(en))
   assert zh[-1]['id'] == en[-1]['id']
   print('OK', zh[-1]['id'])
   "
   ```

## 4. 补K线

`fetch_klines.py`(±2月) 和 `fetch_klines_6m.py`(前10天→后190天) 各自维护一份 `sym_for(e)` 映射
(把 event id/market 映射到 Yahoo ticker), 两个脚本里都要检查/补充映射:

```bash
grep -n "def sym_for" -A 20 fetch_klines.py
grep -n "def sym_for" -A 20 fetch_klines_6m.py
```
- `market == "us_index"` 统一走 `^GSPC`(已有, 不用改)
- `market == "us_stock"`: 检查 `m = {...}` 字典里有没有你的新 ticker(现有只映射了 meta/netflix/nvda/micron/hynix), **没有就在两个脚本里各加一行**, 如:
  ```python
  m = {"meta": "META", ..., "tsla": "TSLA", "aapl": "AAPL"}  # 按 event id 里含的关键词匹配
  ```
  (注意 `sym_for` 是从 `e["id"].lower()` 里找关键词, 加的 key 要能命中你的新 id)
- `market == "crypto"` 且非 UST/LUNA/FTT 特例的, 默认落到 `BTC-USD`, 若新事件是 ETH 单独暴跌, 照 UST/LUNA/FTT 的写法加一个 `if "eth" in e["id"]: return "ETH-USD", "ETH"` 分支

跑两个脚本(会重新抓全量事件, 不是增量, 属正常行为):
```bash
python3 fetch_klines.py     # 输出 "OK  日期 SYM ... bars" / 末尾 "N/N saved, 0 fails"
python3 fetch_klines_6m.py  # 同理
```
**N/N saved 且 0 fails 才算过关**, 有 fail 先查 sym_for 映射对不对, 别跳过硬凑。

## 5. 跑 build.py

```bash
python3 build.py
```
- 会自动打标(TAG_DEFS 正则给每条 precursor 分类)、算路径统计(zero/v/slow/l/hold/fade)、注入双语数据
- **英文构建内置零中文残留硬验证**: 扫描 CJK 字符, 除"中文"切换按钮 2 字外必须零命中, 有漏翻直接 `raise AssertionError` 并打印上下文
- 若报错缺翻译, 是 template 里出现了新的中文 UI 串(不是事件数据本身——事件数据你在第3步已经翻好了), 去 `data/en/ui_strings.json` 按 `{"中文串": "English string"}` 补一条, 再重跑
- 成功输出示例: `built index.html: NNN KB, ...` + `built en/index.html: NNN KB, N events, ...` + `en zh-char scan: N total hits, all within '中文' switch-button label — clean`
- **产物大小核对用 `wc -c index.html`**, 不要信 build.py 打印的 KB 数(那是字符数不是字节数, 中文页会明显偏小, E8 踩过)

## 6. 部署三端 + 实测

```bash
gsync ~/Desktop/skills/crash-radar
```
一键推 GitHub + Gitee 镜像 + 重部署 EdgeOne(读 `.gsync` 里 `EO_SRC="index.html en"`)。

实测(别信部署脚本"success", 必须自己 curl 一遍):
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://crash-radar-uya6gm5g.edgeone.cool/
curl -s -o /dev/null -w "%{http_code}\n" https://crash-radar-uya6gm5g.edgeone.cool/en/
md5 -q index.html; curl -s https://crash-radar-uya6gm5g.edgeone.cool/ | md5
md5 -q en/index.html; curl -s https://crash-radar-uya6gm5g.edgeone.cool/en/ | md5
```
两路都要 200 + 线上 md5 与本地文件 md5 完全一致才算部署成功。

## 7. 收尾: pending 状态 + 回写

1. 把 `pending_events.json` 里对应条目 `status` 改 `done`, 补 `event_id`(第3步定的 id):
   ```python
   import json
   f = "/Users/shaw/.openclaw/workspace/sanliu/crash_radar/pending_events.json"
   d = json.load(open(f))
   for e in d:
       if e["symbol"] == "TSLA" and e["date"] == "2026-07-24":
           e["status"], e["event_id"] = "done", "2026-07-24-tsla-earnings-miss"
   json.dump(d, open(f, "w"), ensure_ascii=False, indent=1)
   ```
   回读验证真改了(A1: 别信写操作自身返回值, 重新读一遍文件确认)。
2. 回写 `~/.openclaw/workspace/workbench/COORD.md`「📌回写同步记录」加一行 + 更新
   `~/.claude/projects/-Users-shaw/memory/kol/project_crash_radar_20260716.md`(事件数+新增日期)。

---

## 已知坑(补录时容易踩)

- **英文/中文事件数组顺序必须对齐**——英文版不重新打标签, 是按中文版同下标复制 tags, 顺序错位=英文页信号标签全部张冠李戴, 静默出错不会报错。
- `fetch_klines.py`/`fetch_klines_6m.py` 是**全量重抓**不是增量, 新 ticker 没进 `sym_for` 映射会直接 `raise ValueError("unmapped stock ...")` 整个脚本失败, 不是只丢那一条。
- `build.py` 的中文残留检查只扫 `en/index.html`, 不代表 `index.html`(中文版)本身没问题——中文版该有中文, 硬验证不适用, 中文版靠人工过一眼数据渲染对不对。
- 数字/信源不许凭记忆编, 每个前兆信号的具体数值必须能对回 `source_url`(反图谋 D3/AS4)。
