#!/usr/bin/env python3
"""把 data/*.json + 快照 + 方法论 注入 template.html → index.html (中文)
   把 data/en/*.json + 同一份快照/K线 + ui_strings 全文替换 注入 template.html → en/index.html (英文)"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
FIXES = [  # 仅用于中文源数据里遗留的英文碎片/错别字修正, 不适用于 en 数据
    ("already情绪偏空", "情绪已偏空"),
    ("彭时", "当时"),
    ("連锁", "连锁"),
    ("financial media", "财经媒体"),
    ("已price in", "已提前计价"),
    ("price in", "提前计价"),
]

def load(name):
    txt = (ROOT / "data" / name).read_text()
    for a, b in FIXES:
        txt = txt.replace(a, b)
    return json.loads(txt)

def load_en(name):
    txt = (ROOT / "data" / "en" / name).read_text()
    return json.loads(txt)

events = load("events_us.json") + load("events_crypto.json")
indicators = load("indicators.json")
klines = json.loads((ROOT / "data" / "klines.json").read_text())
klines6 = json.loads((ROOT / "data" / "klines_6m.json").read_text()) if (ROOT / "data" / "klines_6m.json").exists() else {}

# 事件按日期倒序
events.sort(key=lambda e: e["date"], reverse=True)

# ---- 事后走势路径统计(确定性计算, 全部用收盘价) ----
# 每个事件: 事件日收盘 c0(bars 里首个 >= event_date 的收盘) 与前一日收盘 pre 为基准,
# 算 5/21/63/126 交易日累计涨跌、事后最深回撤/最大反弹、回到 pre 的交易日数、路径分型。
_PATH_N = (5, 21, 63, 126)

def compute_path_stats(klines6_, events_):
    move_of = {e["id"]: e["move_pct"] for e in events_}
    out = {}
    for eid, k in klines6_.items():
        bars = k.get("bars") or []
        if len(bars) < 2 or eid not in move_of:
            continue
        event_date = k["event_date"]
        ei = next((i for i, b in enumerate(bars) if b[0] >= event_date), None)
        if ei is None:
            ei = len(bars) - 1
        c0 = bars[ei][4]
        pre = bars[ei - 1][4] if ei > 0 else c0
        move_pct = move_of[eid]

        rets = {}
        for n in _PATH_N:
            idx = ei + n
            trunc = idx > len(bars) - 1
            idx = min(idx, len(bars) - 1)
            rets[n] = {"v": round((bars[idx][4] / c0 - 1) * 100, 2), "truncated": trunc}

        tail_closes = [b[4] for b in bars[ei:]]
        min_c, max_c = min(tail_closes), max(tail_closes)
        max_dd = round((min_c / c0 - 1) * 100, 2)
        max_ru = round((max_c / c0 - 1) * 100, 2)

        recover_days = None
        rng = range(ei + 1, len(bars))
        if move_pct < 0:  # 暴跌: 首次收盘回到 pre 之上
            for j in rng:
                if bars[j][4] > pre:
                    recover_days = j - ei
                    break
        else:  # 暴涨: 首次收盘跌回 pre 之下(回吐)
            for j in rng:
                if bars[j][4] < pre:
                    recover_days = j - ei
                    break

        end_close = bars[-1][4]
        avail = len(bars) - 1 - ei  # 事件后已有交易日数; 窗口未满时不下终审分型(E9)
        if move_pct < 0:
            if end_close < pre * 0.20:
                path_type = "zero"
            elif recover_days is not None and recover_days <= 42:
                path_type = "v"
            elif recover_days is not None and recover_days <= 126:
                path_type = "slow"
            elif avail < 126:
                path_type = "open"
            else:
                path_type = "l"
        else:
            path_type = "open" if (avail < 63 and recover_days is None) else ("hold" if rets[63]["v"] >= 0 else "fade")

        out[eid] = {
            "ret_5d": rets[5], "ret_21d": rets[21], "ret_63d": rets[63], "ret_126d": rets[126],
            "max_dd": max_dd, "max_ru": max_ru,
            "recover_days": recover_days,
            "path_type": path_type,
        }
    return out

PATH_STATS = compute_path_stats(klines6, events)
(ROOT / "data" / "path_stats.json").write_text(json.dumps(PATH_STATS, ensure_ascii=False, indent=2))
print(f"path stats: {len(PATH_STATS)} events")

PATH_LABELS_ZH = {"zero": "归零/近归零", "v": "V型反转", "slow": "缓慢修复", "l": "L型未修复",
                   "hold": "涨幅站住", "fade": "冲高回落", "open": "观察中·窗口未满"}
PATH_LABELS_EN = {"zero": "Wiped out", "v": "V-shaped rebound", "slow": "Slow repair", "l": "L-shaped, unrecovered",
                   "hold": "Gains held", "fade": "Spike faded", "open": "Still unfolding"}

# ---- 前兆信号 → 标准类别(驱动监控的历史同款匹配) ----
TAG_DEFS = [  # (key, 显示名, 正则) 优先级从上到下, 每条前兆取第一个命中
    ("term", "期限结构", r"期限结构|VIX3M|backwardation|contango"),
    ("vol", "波动率", r"VIX|MOVE|波动率|隐含波动|SKEW"),
    ("cred", "信用与流动性", r"TED|利差|CDS|信用|订单簿|深度|折价|商业票据|国债|流动性|收益率"),
    ("chain", "链上与储备", r"链上|算力|储备|稳定币|Curve|Anchor|LFG|提现|净流出|净流入|钱包|供应量|脱锚|就绪度"),
    ("lev", "杠杆拥挤", r"杠杆|保证金|融资|经纪商贷款|未平仓|OI|套息|持仓|资金费率|ETF|流通盘|爆仓"),
    ("fund", "基本面证伪", r"财报|盈利|指引|下修|订阅|DAU|营收|研究报告|预估|用户增长|资产负债表|技术就绪"),
    ("pol", "政策与监管", r"央行|美联储|加息|降息|监管|警示|禁令|关税|政策|财政|QE|三部|立法|新政|表态|喊单|大流行|旅行禁令|封锁|预警|警告|骚乱|社会阻力|沟通|价格战|评级"),
    ("peer", "同业与传染", r"同业|Snap|联动|传导|外溢|日经|领先|先行|stETH|先例|竞争|MicroStrategy|App Store|排名|传播"),
    ("sent", "情绪与估值", r"恐惧贪婪|Put/Call|市盈率|P/E|估值|溢价|赔率|集中度|基差|过热|投注|敏感度|拥挤度|SEC文件"),
    ("mom", "价格结构", r"暴跌|下跌|熔断|抛售|成交量|超卖|透支|涨幅|前一|闪崩|跌势|转弱|升值|走强|新高|涨跌|波动|裂缝|破位|失效|价格"),
]
TAG_LABELS = {k: v for k, v, _ in TAG_DEFS}

OVERRIDES = [  # (indicator前缀, tag) 修正正则误标, 人工审核结论
    ("地缘政治", "pol"), ("外资抛售美债", "cred"), ("日本央行", "pol"), ("美国7月非农", "pol"),
    ("机构'利好出尽'", "sent"), ("现货ETF审批临近", "sent"), ("股价年内已大幅", "mom"),
    ("微软财报电话会", "peer"), ("币安收购尽调", "peer"), ("LUNA/UST崩溃遗留", "peer"),
    ("此前财报已下修", "fund"), ("退出俄罗斯市场", "pol"), ("同业(Snap)财报预警", "peer"),
    ("组合保险", "lev"), ("MicroStrategy机构购币", "peer"), ("9月零售销售", "pol"),
    ("美联储3月23日", "pol"), ("Curve UST-3pool", "chain"), ("剑桥分析", "pol"),
]

def tag_of(p):
    ind = p.get("indicator", "")
    for pre, t in OVERRIDES:
        if ind.startswith(pre):
            return t
    text = ind + " " + p.get("signal", "")
    for k, _, pat in TAG_DEFS:
        if re.search(pat, text, re.I):
            return k
    return "mom"

NICK_MAP = {"道琼斯工业平均指数": "道指", "标普500指数": "标普500", "Meta Platforms": "META",
            "Micron Technology": "美光", "SK海力士 SK Hynix": "SK海力士", "SK海力士 SK Hy": "SK海力士"}

def nick_of(e):
    a = e["asset"].split("/")[0].split("(")[0].split(",")[0].strip()
    a = NICK_MAP.get(a, a.split(" ")[0][:8])
    return f"{e['date']} {a}"

for e in events:
    for p in e.get("precursors", []):
        p["tag"] = tag_of(p)
    tags = []
    for p in e.get("precursors", []):
        if p["tag"] not in tags:
            tags.append(p["tag"])
    e["tags"] = tags
    e["nick"] = nick_of(e)

# 中文版已算好的 打标结果, 供英文版按 id/下标 直接复制(打标正则是中文的, 不对 en 数据重新跑)
ZH_ID_TO_TAGS = {e["id"]: e["tags"] for e in events}
ZH_ID_TO_PRECURSOR_TAGS = {e["id"]: [p["tag"] for p in e.get("precursors", [])] for e in events}

# 监控用映射: 类别→历史事件 + 事件→类别 (只服务中文监控脚本, 只写一次)
signal_map = {
    "labels": TAG_LABELS,
    "cat_events": {k: [e["nick"] for e in events if k in e["tags"]] for k in TAG_LABELS},
    "event_tags": {e["nick"]: e["tags"] for e in events},
}
(ROOT / "data" / "signal_map.json").write_text(json.dumps(signal_map, ensure_ascii=False, indent=1))
mon_dir = Path.home() / ".openclaw/workspace/sanliu/crash_radar"
if mon_dir.exists():
    (mon_dir / "signal_map.json").write_text(json.dumps(signal_map, ensure_ascii=False))

SNAPSHOT = {
    "built": "2026-07-16",
    "values": {
        "vix_level":               {"v": 16.28, "txt": "16.28"},
        "vix_term_structure":      {"v": 0.861, "txt": "0.861"},
        "skew_index":              {"v": 148.51, "txt": "148.51"},
        "move_index":              {"v": 68.48, "txt": "68.48"},
        "hy_oas":                  {"v": 2.72, "txt": "2.72%"},
        "yield_curve_10y2y":       {"v": 0.42, "txt": "+0.42 pp"},
        "put_call_ratio":          {"v": 0.93, "txt": "0.93 / Eq 0.69"},
        "margin_debt":             {"v": None, "txt": "1.42 万亿$ (05月)"},
        "fear_greed_us":           {"v": 46.9, "txt": "46.9 · 中性"},
        "funding_rate_btc":        {"v": 0.0038, "txt": "+0.0038%/8h"},
        "oi_mcap_ratio":           {"v": None, "txt": "OKX口径 24.4亿$"},
        "futures_basis_annualized":{"v": 3.70, "txt": "+3.7% 年化"},
        "stablecoin_mcap":         {"v": None, "txt": "3035 亿$"},
        "coinbase_premium":        {"v": -0.086, "txt": "-0.086%"},
        "fear_greed_crypto":       {"v": 25, "txt": "25 · 极度恐惧"},
    },
}

# 用本地监控最近一轮采集值刷新快照(30min内新鲜), 美股侧无CORS指标靠这个保准
# (英文版与中文版共用同一份 SNAPSHOT 对象, 数值/中文 txt 之后统一走 ui_strings 全文替换清洗)
_mon_state = mon_dir / "state.json"
if _mon_state.exists():
    try:
        _lv = json.loads(_mon_state.read_text()).get("last_values", {})
        if "vix" in _lv:
            SNAPSHOT["values"]["vix_level"] = {"v": _lv["vix"]["v"], "txt": f"{_lv['vix']['v']:.2f}"}
        if "vix_term" in _lv:
            SNAPSHOT["values"]["vix_term_structure"] = {"v": _lv["vix_term"]["v"], "txt": f"{_lv['vix_term']['v']:.3f}"}
        if "hy_oas" in _lv:
            SNAPSHOT["values"]["hy_oas"] = {"v": _lv["hy_oas"]["v"], "txt": f"{_lv['hy_oas']['v']:.2f}%"}
        if "btc_funding" in _lv:
            SNAPSHOT["values"]["funding_rate_btc"] = {"v": _lv["btc_funding"]["v"], "txt": f"{_lv['btc_funding']['v']:+.4f}%/8h"}
        if "crypto_fng" in _lv:
            SNAPSHOT["values"]["fear_greed_crypto"] = {"v": _lv["crypto_fng"]["v"], "txt": f"{_lv['crypto_fng']['v']} · {_lv['crypto_fng']['label']}"}
        from datetime import date as _d
        SNAPSHOT["built"] = _d.today().isoformat()
        print("snapshot refreshed from monitor state")
    except Exception as _e:
        print("monitor state skip:", _e)

METHOD_HTML = """
<h3>一、34 份档案归纳下来，暴跌只有三种发生机制</h3>
<p><b>① 杠杆踩踏型</b>（1929 / 1987 / 312 / 519 / 2024-08-05 / 2025-10-10 / 海力士）——共同剧本：杠杆或拥挤头寸先堆到极致，任意一根火柴点燃后，强平盘变成新的卖压，价格下跌本身制造更多下跌。这一类<b>事前信号最清晰</b>：保证金债务、资金费率、未平仓合约、套息头寸、杠杆ETF规模，全部量得到。</p>
<p><b>② 外生冲击型</b>（疫情熔断周 / 关税日 / 矿难限电）——触发点本身难预测，但市场对冲击的<b>放大倍数</b>取决于事前脆弱性：同样的坏消息，低杠杆市场跌 3%，高杠杆市场跌 15%。</p>
<p><b>③ 叙事崩塌型</b>（META 的 DAU / Netflix 的订阅数 / NVDA 的 DeepSeek 日 / LUNA-FTX 的信任）——支撑估值的单一叙事被一份财报或一张资产负债表证伪。前兆通常是<b>同业先出事</b>（Snap 先于 Meta、UST 先于 LUNA、FTT 先于 BTC、美光先于海力士），链条上第一家出问题时就该检查手里第二家。</p>
<p>暴涨方向同理：2008-10-13、2020-03-24、2025-04-09 三次史诗级单日大涨全是<b>「暴跌后的政策火箭筒 + 空头回补」</b>，从不发生在平静市场里。</p>

<h3>二、能预警的从来不是「触发器」，而是「脆弱性」</h3>
<p>推文、财报、矿难、央行周日行动——触发器无法预测。但档案里每一次大事故，脆弱性指标至少提前几天到几个月亮灯。实操上用三层组合判断：</p>
<ol>
<li><b>结构脆弱</b>：杠杆堆积（保证金债务新高、资金费率年化&gt;50%、OI 猛增、杠杆ETF膨胀）；</li>
<li><b>情绪极端</b>：恐惧贪婪指数进入两端、Put/Call 过低、基差过热、估值透支；</li>
<li><b>首道裂缝</b>：第一根异常大阴线 / 首次熔断 / 期限结构倒挂 / 同业暴雷——历史上首日暴跌很少是最后一跌（1929-10-24 → 28 → 29；2020-03-09 → 12 → 16；2025-04-03 → 04）。</li>
</ol>
<p>三层同时成立时，减仓不需要预测明天的新闻。</p>

<h3>三、自动监控已上线（配套本页）</h3>
<p>本机脚本每 30 分钟巡检 6 项可自动获取的核心指标，触发阈值直接 iMessage 推送手机，同一指标同级别 12 小时冷却防刷屏：</p>
<div class="rulebox">
VIX ≥28 预警 / ≥36 或单日+50% 红警<br>
VIX/VIX3M ≥0.93 预警 / ≥1.0（倒挂）红警<br>
高收益债利差 ≥4.0% 或 5日+0.5 预警 / ≥5.0% 红警<br>
BTC资金费率 |≥0.05%/8h| 预警 / |≥0.10%| 红警<br>
加密恐惧贪婪 ＜20 或 ≥80 预警 / ≤10 或 ≥90 红警<br>
BTC 24h |≥8%| 预警 / |≥12%| 红警
</div>
<p>数据源全部走免费公开接口（Yahoo / FRED / OKX / alternative.me），连续多轮采集失败会自动发自检消息，不会静默失明。</p>

<h3>四、同样是±10%，事后走势为何分化</h3>
<p>把 34 个事件的事后路径量化（档案展开行的「路径」条：+21d / +63d / 最深回撤 / 恢复天数；分型规则：V型=42个交易日内收复事件前收盘、缓慢修复=126日内、L型=126日未收复、归零=期末价不足事件前20%、窗口未满标「观察中」），再按第一章的三种机制交叉，分化不是随机的：</p>
<table style="width:100%;border-collapse:collapse;font-size:13px;margin:10px 0">
<tr style="border-bottom:1.5px solid #333;text-align:left"><th style="padding:5px 8px">机制</th><th>n</th><th>路径分布</th><th>中位恢复日</th><th>+63d 中位</th></tr>
<tr style="border-bottom:1px solid #ddd"><td style="padding:5px 8px">杠杆踩踏</td><td>14</td><td>V型7 · 缓修复2 · L型4 · 观察中1</td><td>17（10/14恢复）</td><td>+5.3%</td></tr>
<tr style="border-bottom:1px solid #ddd"><td style="padding:5px 8px">外生冲击</td><td>4</td><td><b>V型 4/4</b></td><td>15.5</td><td>+21.9%</td></tr>
<tr style="border-bottom:1px solid #ddd"><td style="padding:5px 8px">叙事崩塌</td><td>9</td><td><b>零V型</b> · 归零3 · L型4 · 缓修复2</td><td>仅2/9恢复过</td><td>−12.4%</td></tr>
<tr style="border-bottom:1px solid #ddd"><td style="padding:5px 8px">政策火箭筒（涨）</td><td>3</td><td>站住2 · 回吐1</td><td>—</td><td>+14.7%</td></tr>
<tr><td style="padding:5px 8px">叙事驱动（涨）</td><td>4</td><td>站住4</td><td>—</td><td>+27.6%</td></tr>
</table>
<p><b>① 第一问定生死：跌的是仓位，还是资产负债表。</b>外生冲击与杠杆踩踏摧毁的是持仓结构——强制卖压出清后资产本身没变，恢复者中位 15–17 个交易日收复失地。叙事崩塌摧毁的是资产的定价前提：9 例<b>无一例 V 型</b>，3 例归零（UST 的锚、LUNA 的增发机制、FTT 的抵押价值——死的是资产负债表本身，+63d 中位 −94%），4 例 L 型（Facebook +63d 仍 −17.2%、META −12.4%，DAU 与订阅叙事要等几个季度的财报才能重建）。<b>价格错了会被买回来，前提没了不会。</b></p>
<p><b>② 外生冲击 4/4 全 V 型，不全是运气。</b>冲击不损伤偿付能力，且大冲击会逼出政策响应：2020-03 两次熔断后 3 月 23 日美联储无限 QE（17–19 天收复），2025-04-04 关税冲击 3 天后暂停落地。政策速度同样解释踩踏组内部的分层——2024-08-05 日元套息（日银次日安抚）3–6 天收复、2010 闪崩 4 天，而 1929 / 1987 / 2025-10-10 没有等来火箭筒，全部 L 型。诚实一笔：2008-10-15 虽 14 天收复单日缺口，+63d 仍 −7.1%——<b>收复单日跌幅≠脱离熊市</b>。</p>
<p><b>③ 杠杆出清是快反弹的前提，但出清≠修复。</b>踩踏组 7 例 V 型都发生在强平盘一次性出清后（312 虽 48 天才收复，+63d 已 +95.8%）；4 例 L 型的共性是杠杆是结构性、分层瓦解的——1929 保证金贷款、1987 组合保险、2021-04 隐藏合约杠杆、2025-10-10 山寨币杠杆，第一波强平只是开始。</p>
<p><b>④ 暴涨方向镜像成立。</b>政策火箭筒打在趋势转折点则站住（2020-03-24 +63d +28.0%、2025-04-09 +14.7%），打在熊市半山腰则回吐（2008-10-13 单日 +11.1%，7 天后全数回吐，+63d −13.1%——政策修复不了偿付能力时，大涨只是空头回补）。</p>
<p><b>实操：暴跌当天先答四个问题</b>——①偿付能力有没有洞（储备 / 兑付 / 资产负债表，有洞别接）；②强制卖压出清了没有（资金费率归位、保证金债务下降、OI 骤减 → 踩踏近尾声）；③政策工具箱会不会开、多快（明确响应 → V 型概率大增）；④被打击的叙事可否被下一份财报修复（能 → 缓修复，不能 → L 型）。与第二章「三层脆弱性」衔接：脆弱性判断<b>会不会跌</b>，这四问判断<b>跌完怎么走</b>。</p>
<p>本章局限：34 例小样本，机制归类含主观判断（逐事件归类与数字可在 data/path_stats.json 复核）；分型窗口为事件后 126 个交易日——1929-10-29 在窗口内计为 V 型（次日反弹且半年内有修复浪），但 1932 年 −89% 的大底在窗口之外，二次崩塌本表不捕捉；入档的都是载入史册的大事件，「跌 10% 后无事发生」的样本不在库里（幸存者偏差）。</p>

<h3>五、诚实的局限</h3>
<ul>
<li>脆弱性指标提高的是<b>胜率与仓位纪律</b>，不是水晶球——1987 与 2010 闪崩这类内生流动性踩踏，事前信号很弱，兜底只能靠仓位；</li>
<li>交易所净流入、Glassnode 杠杆率、市场宽度三项没有免费程序化数据源，仪表盘标为「人工核查项」，附人工入口；</li>
<li>阈值参考历史分位与研究文献设定，市场结构会漂移，建议每季度对着新事件回看一次。</li>
</ul>
"""

if "--tags" in sys.argv:  # 打标抽查
    for e in events:
        print(e["nick"], e["tags"])
        for p in e.get("precursors", []):
            print("   ", p["tag"].ljust(5), p["indicator"][:34])
    sys.exit(0)

tpl = (ROOT / "template.html").read_text()

# ============ 中文构建 (原逻辑不变, 只多语言按钮两个占位符) ============
out = tpl.replace("/*__EVENTS__*/[]", json.dumps(events, ensure_ascii=False))
out = out.replace("/*__INDICATORS__*/[]", json.dumps(indicators, ensure_ascii=False))
out = out.replace("/*__SNAPSHOT__*/{}", json.dumps(SNAPSHOT, ensure_ascii=False))
out = out.replace("/*__KLINES__*/{}", json.dumps(klines, ensure_ascii=False))
out = out.replace("/*__KLINES6__*/{}", json.dumps(klines6, ensure_ascii=False))
out = out.replace("/*__TAGLABELS__*/{}", json.dumps(TAG_LABELS, ensure_ascii=False))
out = out.replace("/*__PATHSTATS__*/{}", json.dumps(PATH_STATS, ensure_ascii=False))
out = out.replace("/*__PATHLABELS__*/{}", json.dumps(PATH_LABELS_ZH, ensure_ascii=False))
out = out.replace('/*__METHOD__*/""', json.dumps(METHOD_HTML, ensure_ascii=False))
for ph in ("__EVENTS__", "__INDICATORS__", "__SNAPSHOT__", "__KLINES__", "__KLINES6__", "__TAGLABELS__", "__PATHSTATS__", "__PATHLABELS__", "__METHOD__"):
    assert ph not in out, ph
out = out.replace("%%LANG_HREF%%", "en/")
out = out.replace("%%LANG_LABEL%%", "EN")
assert "%%LANG_" not in out
(ROOT / "index.html").write_text(out)
print(f"built index.html: {len(out)//1024} KB, {len(events)} events, {len(indicators)} indicators, {len(klines)} klines")

# ============ 英文构建 ============
events_en = load_en("events_us.json") + load_en("events_crypto.json")
events_en.sort(key=lambda e: e["date"], reverse=True)

for e in events_en:
    zh_tags = ZH_ID_TO_TAGS.get(e["id"])
    zh_ptags = ZH_ID_TO_PRECURSOR_TAGS.get(e["id"])
    assert zh_tags is not None, f"en event id not found in zh: {e['id']}"
    precursors = e.get("precursors", [])
    assert len(precursors) == len(zh_ptags), f"precursor count mismatch for {e['id']}: en={len(precursors)} zh={len(zh_ptags)}"
    for p, t in zip(precursors, zh_ptags):
        p["tag"] = t
    e["tags"] = zh_tags

NICK_MAP_EN = {"Dow Jones Industrial Average": "Dow", "S&P 500": "S&P 500", "S&P 500 Index": "S&P 500",
               "Meta Platforms": "META", "Micron Technology": "Micron", "SK Hynix": "SK Hynix"}

def nick_of_en(e):
    a = e["asset"].split("/")[0].split("(")[0].split(",")[0].strip()
    a = NICK_MAP_EN.get(a, a.split(" ")[0][:8])
    return f"{e['date']} {a}"

for e in events_en:
    e["nick"] = nick_of_en(e)

indicators_en = load_en("indicators.json")

TAG_LABELS_EN = {
    "term": "Term Structure", "vol": "Volatility", "cred": "Credit & Liquidity",
    "chain": "On-chain & Reserves", "lev": "Leverage Crowding", "fund": "Fundamentals Falsified",
    "pol": "Policy & Regulation", "peer": "Peers & Contagion", "sent": "Sentiment & Valuation",
    "mom": "Price Structure",
}

METHOD_HTML_EN = (ROOT / "data" / "en" / "method.html").read_text()

out_en = tpl.replace("/*__EVENTS__*/[]", json.dumps(events_en, ensure_ascii=False))
out_en = out_en.replace("/*__INDICATORS__*/[]", json.dumps(indicators_en, ensure_ascii=False))
out_en = out_en.replace("/*__SNAPSHOT__*/{}", json.dumps(SNAPSHOT, ensure_ascii=False))  # 共用同一份
out_en = out_en.replace("/*__KLINES__*/{}", json.dumps(klines, ensure_ascii=False))       # 共用同一份
out_en = out_en.replace("/*__KLINES6__*/{}", json.dumps(klines6, ensure_ascii=False))     # 共用同一份
out_en = out_en.replace("/*__TAGLABELS__*/{}", json.dumps(TAG_LABELS_EN, ensure_ascii=False))
out_en = out_en.replace("/*__PATHSTATS__*/{}", json.dumps(PATH_STATS, ensure_ascii=False))  # 共用同一份(纯数字, 无中文)
out_en = out_en.replace("/*__PATHLABELS__*/{}", json.dumps(PATH_LABELS_EN, ensure_ascii=False))
out_en = out_en.replace('/*__METHOD__*/""', json.dumps(METHOD_HTML_EN, ensure_ascii=False))
for ph in ("__EVENTS__", "__INDICATORS__", "__SNAPSHOT__", "__KLINES__", "__KLINES6__", "__TAGLABELS__", "__PATHSTATS__", "__PATHLABELS__", "__METHOD__"):
    assert ph not in out_en, ph

# ui_strings: zh→en UI 替换表, 按 key 长度降序对整份输出 HTML 全文替换
# (顺带把 SNAPSHOT/klines 里残留的中文 txt/label 一起清掉)
ui_strings = json.loads((ROOT / "data" / "en" / "ui_strings.json").read_text())
for k in sorted(ui_strings.keys(), key=len, reverse=True):
    if k in out_en:
        out_en = out_en.replace(k, ui_strings[k])

out_en = out_en.replace('lang="zh-CN"', 'lang="en"')

# POLISH: 逐条替换清不掉的中英组合瑕疵(单位+标签语义重复/多余空格), 目标串必须逐字节存在
POLISH = [
    ('"Events Archived"," events"', '"Events Archived",""'),
    ('"Year Span Covered"," yrs"', '"Years Covered",""'),
    ("Total  ${EVENTS.length}  archived events", "${EVENTS.length} archived events"),
]
for a, b in POLISH:
    assert a in out_en, f"POLISH miss: {a}"
    out_en = out_en.replace(a, b)

# 语言切换占位符放在 ui_strings 替换之后处理: 避免 "中文" 二字被表误伤(表里确认无此 key)
out_en = out_en.replace("%%LANG_HREF%%", "../")
out_en = out_en.replace("%%LANG_LABEL%%", "中文")
assert "%%LANG_" not in out_en

# ---- 硬验证: 中文残留扫描, 除"中文"切换按钮 2 字外必须零命中 ----
_zh_pat = re.compile(r"[一-鿿　-〿＀-￯]")
_matches = list(_zh_pat.finditer(out_en))
_zw_positions = set()
for m in re.finditer("中文", out_en):
    _zw_positions.add(m.start())
    _zw_positions.add(m.start() + 1)
_leaks = [m for m in _matches if m.start() not in _zw_positions]
if _leaks:
    print(f"!! {len(_leaks)} 处中文残留 (总命中 {len(_matches)}, 语言按钮豁免 {len(_zw_positions)}):")
    seen = set()
    for m in _leaks:
        ctx = out_en[max(0, m.start() - 60): m.start() + 60]
        if ctx in seen:
            continue
        seen.add(ctx)
        print("  ...", ctx.replace("\n", " "), "...")
    raise AssertionError(f"{len(_leaks)} chinese chars leaked into en build (see context above)")
print(f"en zh-char scan: {len(_matches)} total hits, all within '中文' switch-button label — clean")

(ROOT / "en").mkdir(parents=True, exist_ok=True)
(ROOT / "en" / "index.html").write_text(out_en)
print(f"built en/index.html: {len(out_en)//1024} KB, {len(events_en)} events, {len(indicators_en)} indicators, {len(klines)} klines")
