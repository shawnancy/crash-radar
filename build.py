#!/usr/bin/env python3
"""把 data/*.json + 快照 + 方法论 注入 template.html → index.html"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
FIXES = [
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

events = load("events_us.json") + load("events_crypto.json")
indicators = load("indicators.json")
klines = json.loads((ROOT / "data" / "klines.json").read_text())
klines6 = json.loads((ROOT / "data" / "klines_6m.json").read_text()) if (ROOT / "data" / "klines_6m.json").exists() else {}

# 事件按日期倒序
events.sort(key=lambda e: e["date"], reverse=True)

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

# 监控用映射: 类别→历史事件 + 事件→类别
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

<h3>四、诚实的局限</h3>
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
out = tpl.replace("/*__EVENTS__*/[]", json.dumps(events, ensure_ascii=False))
out = out.replace("/*__INDICATORS__*/[]", json.dumps(indicators, ensure_ascii=False))
out = out.replace("/*__SNAPSHOT__*/{}", json.dumps(SNAPSHOT, ensure_ascii=False))
out = out.replace("/*__KLINES__*/{}", json.dumps(klines, ensure_ascii=False))
out = out.replace("/*__KLINES6__*/{}", json.dumps(klines6, ensure_ascii=False))
out = out.replace("/*__TAGLABELS__*/{}", json.dumps(TAG_LABELS, ensure_ascii=False))
out = out.replace('/*__METHOD__*/""', json.dumps(METHOD_HTML, ensure_ascii=False))
for ph in ("__EVENTS__", "__INDICATORS__", "__SNAPSHOT__", "__KLINES__", "__KLINES6__", "__TAGLABELS__", "__METHOD__"):
    assert ph not in out, ph
(ROOT / "index.html").write_text(out)
print(f"built index.html: {len(out)//1024} KB, {len(events)} events, {len(indicators)} indicators, {len(klines)} klines")
