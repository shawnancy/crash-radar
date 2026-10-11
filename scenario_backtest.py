#!/usr/bin/env python3
"""
scenario_backtest — 情景推演数据引擎 (2026-07-28 新增)

给「情景推演」板块供数: 对每个有历史序列的前瞻指标, 找出历史上它越过预警线的
全部独立事件, 统计事件之后标的(标普/BTC)的前瞻路径分位数, 产出:
    data/scenario_stats.json   ← 页面构建期注入

口径与诚实性约定 (必须随数字一起展示, 别只给中位数):
  · 独立事件定义 = 指标越过阈值, 且距上一次越线 >GAP 个交易日(默认10), 避免同一轮行情重复计数
  · 路径 = 以事件日收盘价为 100, 之后 0..126 个交易日的累计涨跌%, 取中位/25分位/75分位/最差
  · 同时给出「基准」= 同期全部交易日的无条件前瞻路径。**指标路径没显著优于基准就是没信息量**
  · 样本数 n 一律随统计量输出, 页面必须显示 n, n<20 的标【线索】不标【实证】

数据源 (全部 curl/urllib 实证可得, 见各 fetch 函数注释):
  ^VIX ^VIX3M ^SKEW ^MOVE ^GSPC  → Yahoo v8 chart (需 period1/period2, range=max 会被降频到季线)
  T10Y2Y BAA10Y                  → FRED fredgraph.csv (E3: 必须走 curl 子进程, urllib 会挂起)
  加密恐惧贪婪                     → alternative.me (2018-02 起 3000+ 点)
  BTC-USD                        → Yahoo

注意 HY OAS: FRED 免费 CSV 对 BAMLH0A0HYM2 只给最近 3 年(cosd/coed 均被忽略, 已实测),
无法回测。此处用 BAA10Y(穆迪Baa企业债 - 10Y国债, 1986起全量) 作信用利差代理, 触发线改用
自身历史 85 分位, 页面上必须标注「代理口径」, 不要冒充 HY OAS 本体。
"""
import json, subprocess, sys, time, urllib.parse, urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "series_cache.json"
OUT = ROOT / "data" / "scenario_stats.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
HORIZONS = list(range(0, 127))
GAP = 10           # 独立事件最小间隔(交易日)
THRESH = {}        # main() 里从 data/thresholds.json 载入
START = "2004-01-01"


def _get(url, timeout=40):
    """Yahoo/alternative.me 用。退避到 60s 级别 —— Yahoo 对同 IP 密集拉历史会返 429,
    且惩罚期是分钟级不是秒级, 短退避(3s/8s)一路撞穿毫无意义(07-28 实测)。"""
    last = None
    for delay in (0, 5, 20, 60):
        if delay:
            time.sleep(delay)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode()
        except Exception as e:
            last = e
    raise RuntimeError(f"{url.split('/')[2]}: {last}")


def _curl(url, timeout=40):
    """FRED 专用。两条硬性约束, 都是实测出来的:
    ① urllib 会被 FRED 的客户端指纹挂住(带UA也没用), curl 秒通 —— memory E3
    ② **绝对不能传浏览器 UA**: 07-28 实测 `curl -A "Mozilla/5.0 ... Chrome/121"` 直接
       连接被掐(http=000 零字节), 而 curl 默认 UA(curl/8.x) 稳定 200。跟常见反爬方向相反,
       FRED 这个 CSV 端点是"认机器不认浏览器"。别好心给它加 UA。"""
    last = None
    for delay in (0, 3, 8):
        if delay:
            time.sleep(delay)
        p = subprocess.run(["curl", "-sm", str(timeout), url], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.startswith("observation_date"):
            return p.stdout
        last = f"rc={p.returncode} head={p.stdout[:40]!r}"
    raise RuntimeError(f"curl failed: {url} ({last})")


def fetch_yahoo(symbol):
    """日线收盘序列。必须显式传 period1/period2 —— range=max&interval=1d 会被 Yahoo
    降频成季线(实测 ^GSPC 只回 168 行 dataGranularity=3mo), 是个静默陷阱。"""
    p1 = int(time.mktime(time.strptime(START, "%Y-%m-%d")))
    p2 = int(time.time())
    enc = urllib.parse.quote(symbol, safe="")
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        try:
            raw = _get(f"https://{host}/v8/finance/chart/{enc}"
                       f"?period1={p1}&period2={p2}&interval=1d")
            r = json.loads(raw)["chart"]["result"][0]
            out = {time.strftime("%Y-%m-%d", time.gmtime(t)): c
                   for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c}
            if len(out) > 500:
                return out
        except Exception as e:
            print(f"  {symbol}@{host} 失败: {e}")
    raise RuntimeError(f"yahoo {symbol} 全部主机失败")


def fetch_fred(series_id):
    csv = _curl(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={START}")
    out = {}
    for line in csv.strip().splitlines()[1:]:
        d, v = line.split(",")[0], line.split(",")[1]
        if v not in (".", ""):
            out[d] = float(v)
    return out


def fetch_crypto_fng():
    d = json.loads(_get("https://api.alternative.me/fng/?limit=0&format=json"))["data"]
    return {time.strftime("%Y-%m-%d", time.gmtime(int(x["timestamp"]))): float(x["value"])
            for x in d}


SERIES_SPEC = {
    "spx":       ("yahoo", "^GSPC"),
    "btc":       ("yahoo", "BTC-USD"),
    "vix":       ("yahoo", "^VIX"),
    "vix3m":     ("yahoo", "^VIX3M"),
    "skew":      ("yahoo", "^SKEW"),
    "move":      ("yahoo", "^MOVE"),
    "t10y2y":    ("fred", "T10Y2Y"),
    "baa10y":    ("fred", "BAA10Y"),
    "crypto_fng": ("fng", None),
}


def load_series(refresh=False):
    cache = {}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text())
        if cache.get("_start") == START:
            age = (date.today() - date.fromisoformat(cache.get("_date", "2000-01-01"))).days
            if age < 1:
                print(f"用缓存 series_cache.json ({cache.get('_date')})")
                return {k: v for k, v in cache.items() if not k.startswith("_")}
    out = {}
    for key, (kind, sym) in SERIES_SPEC.items():
        old = cache.get(key, {})
        try:
            if kind == "yahoo":
                got = fetch_yahoo(sym)
            elif kind == "fred":
                got = fetch_fred(sym)
            else:
                got = fetch_crypto_fng()
        except Exception as e:
            print(f"  {key:<11} 拉取失败: {e}")
            got = {}
        # 合并: 新拉到的覆盖同日, 缓存里更早的历史保留 (Yahoo 限流时不至于把历史丢光)
        merged = dict(old)
        merged.update(got)
        out[key] = merged
        if merged:
            ks = sorted(merged)
            src = "实时" if got else ("缓存" if old else "空")
            print(f"  {key:<11} {len(merged):>5} 条  {ks[0]} → {ks[-1]}  [{src}"
                  f"{'+缓存补' + str(len(merged) - len(got)) if got and len(merged) > len(got) else ''}]")
        else:
            print(f"  {key:<11} 无数据(既没拉到也没缓存)")
    out["_date"] = date.today().isoformat()
    out["_start"] = START
    CACHE.write_text(json.dumps(out))
    return {k: v for k, v in out.items() if not k.startswith("_")}


# ---------- 指标定义 ----------
# fn: 由原始序列算出该指标的日频值; target: 前瞻路径看哪个标的
# trig: (方向, 预警线, 红警线); 方向 "ge"=大于等于触发, "le"=小于等于触发
# proxy: 非真身口径时的诚实标注(会随统计量一起进 json, 页面必须显示)
INDICATOR_SPEC = {
    "vix_level": dict(
        target="spx", thr_id="vix_level",
        fn=lambda s: s["vix"],
        why="VIX 是标普 30 天期权隐含波动率。走高 = 期权市场为未来一个月的大幅波动付更高价钱，"
            "通常由三种力量推高：机构批量买保护、做市商被迫对冲、以及现货本身开始剧烈跳动。"),
    "vix_term_structure": dict(
        target="spx", thr_id="vix_term_structure",
        fn=lambda s: {d: s["vix"][d] / s["vix3m"][d] for d in s["vix"] if d in s["vix3m"]},
        why="近月(30天)波动率除以远月(93天)。正常牛市里远月更贵(contango，比值 0.85 上下)，"
            "因为不确定性随时间累积。比值上冲说明恐慌集中在最近一个月——买保护的钱在抢近端，"
            "这是'马上要出事'的定价，而不是'长期变差'的定价。破 1.0 叫倒挂，历史上多次精确标记危机启动点。"),
    "skew_index": dict(
        target="spx", thr_id="skew_index",
        fn=lambda s: s["skew"],
        why="SKEW 衡量深度虚值看跌期权相对平值的贵贱，也就是市场为'黑天鹅'付的溢价。"
            "它高说明有人在买很远的下跌保护。注意：SKEW 长期在 135-155 区间，单独高位不等于崩盘将至，"
            "它更像'保险变贵了'，要配合 VIX 同步走高才构成双确认。"),
    "move_index": dict(
        target="spx", thr_id="move_index",
        fn=lambda s: s["move"],
        why="MOVE 是美债版的 VIX，衡量国债期权隐含波动率。债市是所有资产的定价锚，"
            "MOVE 飙升意味着无风险利率本身不稳，会通过折现率、抵押品估值、跨资产风险预算三条路径传导到股票。"
            "历史上债市波动率先于股市波动率见顶的次数不少。"),
    "hy_oas": dict(
        target="spx", thr_id="hy_oas", direction="ge", pct_trig=(0.85, 0.95),
        fn=lambda s: s["baa10y"],
        proxy="用 BAA10Y(穆迪Baa企业债收益率-10年美债, FRED 1986起全量) 代理高收益债利差。"
              "真身 BAMLH0A0HYM2 的 FRED 免费 CSV 只给最近 3 年(cosd/coed 参数被忽略, 已实测), 无法回测。"
              "触发线因此改用自身历史 85/95 分位, 不是页面上 5.0%/8.0% 的 HY OAS 口径。",
        why="信用利差是最诚实的压力计。股票可以靠叙事撑估值，但债券持有人只关心能不能还上钱。"
            "利差走阔 = 市场开始怀疑企业偿付能力，这是'跌资产负债表'而不是'跌仓位'的前兆，"
            "档案里叙事崩塌型事件事后无一例 V 型反弹，根子就在这里。"),
    "yield_curve_10y2y": dict(
        target="spx", thr_id="yield_curve_10y2y",
        fn=lambda s: s["t10y2y"],
        why="10年减2年美债收益率。倒挂(<0)意味着市场认为短期利率高于长期——通常是货币政策已经收得过紧、"
            "衰退预期在定价。注意它是个'慢'指标：历史上倒挂到衰退的时滞可长达 12-24 个月，"
            "而且真正的股市高点往往出现在**倒挂结束、曲线重新陡峭化**之后，不是倒挂当时。"),
    "fear_greed_crypto_low": dict(
        target="btc", thr_id="fear_greed_crypto", band_side="lo", label="加密恐惧贪婪(极度恐惧端)",
        fn=lambda s: s["crypto_fng"],
        why="加密恐惧贪婪指数跌进 20 以下 = 极度恐惧。加密市场散户占比高、杠杆清算快，"
            "情绪指标的均值回复性比股市强得多，历史上极度恐惧区更多是阶段性底部区域而不是崩盘起点——"
            "但这条统计规律建立在 2018 年以来的样本上，只覆盖了两轮完整周期。"),
    "fear_greed_crypto_high": dict(
        target="btc", thr_id="fear_greed_crypto", band_side="hi", label="加密恐惧贪婪(极度贪婪端)",
        fn=lambda s: s["crypto_fng"],
        why="指数冲上 80 = 极度贪婪。这一端的风险在于它常与杠杆堆积同时出现(资金费率飙高、OI 猛增)，"
            "构成档案里'杠杆踩踏型'暴跌的标准前置条件。"),
}


def pctile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = q * (len(sorted_vals) - 1)
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def path_stats(anchor_idx, prices):
    """给一组事件的索引, 算 0..126 交易日的累计涨跌%分位"""
    out = {}
    for h in HORIZONS:
        vals = sorted((prices[i + h] / prices[i] - 1) * 100
                      for i in anchor_idx if i + h < len(prices))
        if len(vals) < 3:
            break
        out[h] = {"n": len(vals),
                  "med": round(pctile(vals, .5), 2),
                  "p25": round(pctile(vals, .25), 2),
                  "p75": round(pctile(vals, .75), 2),
                  "min": round(vals[0], 2),
                  "max": round(vals[-1], 2),
                  "pos": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1)}
    return out


def episodes_of(vals_by_day, days, direction, thr):
    """指标越线的独立事件索引 (间隔 > GAP 交易日才算新事件)"""
    eps, last = [], -999
    for i, d in enumerate(days):
        v = vals_by_day.get(d)
        if v is None:
            continue
        hit = (v >= thr) if direction == "ge" else (v <= thr)
        if hit:
            if i - last > GAP:
                eps.append(i)
            last = i
    return eps


def main():
    global THRESH
    THRESH = json.loads((ROOT / "data" / "thresholds.json").read_text())["indicators"]
    print(f"阈值单一信源: data/thresholds.json ({len(THRESH)} 指标)")
    print("=== 拉取历史序列 ===")
    S = load_series(refresh="--refresh" in sys.argv)
    for k in ("spx", "btc"):
        if len(S.get(k, {})) < 500:
            print(f"致命: {k} 序列不足, 中止"); sys.exit(1)

    targets = {"spx": S["spx"], "btc": S["btc"]}
    result = {"generated": date.today().isoformat(), "gap": GAP, "indicators": {}, "base": {}}
    for tk, tv in targets.items():
        ks = sorted(tv)
        result["base"][tk] = {"last": round(tv[ks[-1]], 2), "date": ks[-1]}

    print("\n=== 逐指标回测 ===")
    for iid, spec in INDICATOR_SPEC.items():
        try:
            series = spec["fn"](S)
        except Exception as e:
            print(f"  {iid:<24} 跳过(算不出): {e}"); continue
        if len(series) < 300:
            print(f"  {iid:<24} 跳过(样本{len(series)}不足)"); continue
        tgt = spec["target"]
        days = sorted(d for d in targets[tgt] if d in series)
        prices = [targets[tgt][d] for d in days]
        if len(days) < 300:
            print(f"  {iid:<24} 跳过(与标的重合日{len(days)}不足)"); continue

        # 阈值一律从 data/thresholds.json 取 —— 这里绝不能再写第二份数字。
        # (本文件初版就犯过: VIX 硬写 25 而阈值表是 28, 正是本次要根治的漂移。)
        thr = THRESH.get(spec["thr_id"])
        if not thr:
            print(f"  {iid:<24} 跳过: thresholds.json 里没有 {spec['thr_id']}"); continue
        if spec.get("pct_trig"):                       # 分位口径(hy_oas 代理, 见 proxy 字段)
            direction = spec.get("direction", "ge")
            sv = sorted(series[d] for d in days)
            warn = round(pctile(sv, spec["pct_trig"][0]), 3)
            danger = round(pctile(sv, spec["pct_trig"][1]), 3)
        elif spec.get("band_side"):                    # band 类指标的某一侧(如恐贪的极度恐惧端)
            side = spec["band_side"]
            direction = "le" if side == "lo" else "ge"
            warn, danger = thr.get(f"warn_{side}"), thr.get(f"danger_{side}")
        else:
            direction = thr["kind"]
            if direction not in ("ge", "le"):
                print(f"  {iid:<24} 跳过: kind={direction} 不支持回测"); continue
            warn, danger = thr.get("warn"), thr.get("danger")
        if warn is None:
            print(f"  {iid:<24} 跳过: 取不到预警线"); continue

        rec = {"target": tgt, "direction": direction, "warn": warn, "danger": danger,
               "label": spec.get("label"), "why": spec["why"], "proxy": spec.get("proxy"),
               "span": [days[0], days[-1]]}
        for lvl, thr in (("warn", warn), ("danger", danger)):
            eps = episodes_of(series, days, direction, thr)
            rec[lvl + "_n"] = len(eps)
            rec[lvl + "_path"] = path_stats(eps, prices)
            rec[lvl + "_dates"] = [days[i] for i in eps][-40:]
        # 基准: 同期全部交易日无条件路径
        rec["base_path"] = path_stats(list(range(len(prices) - 130)), prices)
        result["indicators"][iid] = rec
        w = rec["warn_path"].get(21, {})
        b = rec["base_path"].get(21, {})
        print(f"  {iid:<24} 预警线={warn:<8} 事件{rec['warn_n']:>3}次 | "
              f"+21日中位 {w.get('med', 0):+6.2f}% (基准 {b.get('med', 0):+.2f}%) 正{w.get('pos', 0):>5.1f}%")

    # ---- 动态阈值 (07-29, 用户批准 SKEW 改滚动分位) ----
    # 固定阈值遇到时代漂移会失效: SKEW 中位 2010=120 → 2024=148, 固定线 145 在 2007-2013
    # 触发天数占比 0.0%、在 2024 占 61% —— 同一条线从"十年一遇"变成"抛硬币"。
    # 改成"对比自身过去 N 年的分位", 阈值自动跟着分布漂。
    # 产出overlay文件, 由 monitor 与 build.py 合并进 thresholds.json, **不改动手工维护的源文件**。
    dyn = {}
    for iid, spec in (THRESH.items()):
        d = spec.get("dynamic")
        if not d or d.get("method") != "rolling_pct":
            continue
        skey = d["series"]
        ser = S.get(skey) or {}
        ks = sorted(ser)
        win = d.get("window_days", 504)
        if len(ks) < win + 20:
            print(f"  动态阈值 {iid}: 序列不足({len(ks)}<{win+20}), 保留静态值"); continue
        w = sorted(ser[k] for k in ks[-win:])
        warn = round(pctile(w, d["warn_q"]), 1)
        danger = round(pctile(w, d["danger_q"]), 1)
        cur = ser[ks[-1]]
        dyn[iid] = {"warn": warn, "danger": danger, "asof": ks[-1], "current": cur,
                    "window_days": win, "warn_q": d["warn_q"], "danger_q": d["danger_q"]}
        print(f"  动态阈值 {iid}: 预警线 {warn} / 红警线 {danger} "
              f"(过去{win}交易日的{int(d['warn_q']*100)}/{int(d['danger_q']*100)}分位), "
              f"现值 {cur} → {'亮' if cur >= warn else '不亮'}")
    (ROOT / "data" / "thresholds_dynamic.json").write_text(
        json.dumps({"generated": date.today().isoformat(), "indicators": dyn}, ensure_ascii=False, indent=1))
    mon = Path.home() / ".openclaw/workspace/sanliu/crash_radar"
    if mon.exists():
        (mon / "thresholds_dynamic.json").write_text(
            json.dumps({"generated": date.today().isoformat(), "indicators": dyn}, ensure_ascii=False))

    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"\n写出 {OUT}  ({OUT.stat().st_size // 1024} KB, {len(result['indicators'])} 个指标)")


if __name__ == "__main__":
    main()
