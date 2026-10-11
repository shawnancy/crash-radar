#!/usr/bin/env python3
"""
analog_forecast — 全指标类比推演 (2026-07-28)

回答的问题: "把当前所有指标合起来看, 后面每天/半年可能怎么走"。

方法 (类比集合 / analog ensemble, 不是模型外推):
  ① 把每个指标转成**滚动分位**(对比自己过去3年), 而不是绝对值 ——
     绝对值会被时代漂移毁掉: SKEW 中位从 2010 的 120 漂到 2024 的 148,
     "SKEW≥145" 在 2010 是十年一遇、在 2024 是家常便饭。分位口径才可比。
  ② 今天 = 一个 10 维状态向量; 在历史上找欧氏距离最近的 K 个日子(互相间隔≥21交易日,
     避免同一轮行情被重复计入)。
  ③ 把这 K 个日子之后 126 个交易日的**真实日线路径**按今天的价位重新换算, 叠成分位扇形。
     所以图上每一天都是真实发生过的走势的集合, 没有任何一根是"模型画出来的"。

⚠ 最要紧的一步 —— 方法自检 (walk-forward):
  在历史上每隔一段时间取一个评估日, **只用该日之前的数据**做同样的推演, 再和真实结果对账:
    · 方向命中率 vs 无条件基准 (中位数指对了方向吗)
    · 区间覆盖率 (真实路径落在 25-75 / 10-90 分位带里的比例, 理想是 50% / 80%)
    · 中位数的绝对误差 vs "无条件历史中位"这个笨基准 (打不过笨基准 = 这套没用)
  结论直接写进产出 json 并显示在页面上。没有预测力就明说没有, 不许只给漂亮的扇形图。

产出: data/analog_forecast.json  → 页面「半年推演」板块
"""
import json, statistics as st, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "series_cache.json"
OUT = ROOT / "data" / "analog_forecast.json"

HORIZON = 126        # 半年 ≈ 126 个交易日
PCT_WIN = 756        # 滚动分位窗口 = 3 年
K = 40               # 取最近的 K 个类比日
MIN_SEP = 21         # 类比日之间至少间隔 21 交易日 (不让同一轮行情重复计数)
WARMUP = PCT_WIN + 260


def pctile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = q * (len(sorted_vals) - 1)
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def rolling_rank(vals, win=PCT_WIN):
    """每个点在自己过去 win 个点里的分位 (0..1)。严格只用过去, 无前视。"""
    out = [None] * len(vals)
    for i in range(win, len(vals)):
        w = vals[i - win:i]
        c = sum(1 for x in w if x <= vals[i])
        out[i] = c / len(w)
    return out


def build_features(S, target="spx"):
    """返回 (days, px, feats) —— feats[i] = {名: 分位}"""
    px_src = S[target]
    need = ["vix", "vix3m", "skew", "move", "baa10y", "t10y2y"]
    # 各指标的更新节奏不齐(MOVE 的 Yahoo 日线常滞后一周, FRED 滞后1-2天)。
    # 若强求"当天六项全有", 锚点会被最慢的那一项拖到一周前 —— 实测把推演起点卡在
    # 07-17 而标的已经走到 07-27, 起点差 0.6%, 用户看图会直接对不上。
    # 改为**前向填充**(用最后一个已知值), 最多容忍 MAX_STALE 个交易日, 并记录实际滞后天数。
    MAX_STALE = 10
    all_days = sorted(px_src)
    ff, stale = {}, {}
    for k in need:
        src = S[k]; sd = sorted(src)
        vals, si, last, lastd = {}, 0, None, None
        for d in all_days:
            while si < len(sd) and sd[si] <= d:
                last, lastd = src[sd[si]], sd[si]; si += 1
            if last is not None:
                vals[d] = (last, lastd)
        ff[k] = vals
        if all_days and all_days[-1] in vals:
            gap = sum(1 for d in all_days if vals[all_days[-1]][1] < d <= all_days[-1])
            stale[k] = gap
    days = [d for d in all_days if all(d in ff[k] for k in need)]
    # 尾部若某项滞后过久, 宁可把锚点往回退, 也不拿一周前的值冒充今天
    while days and any(sum(1 for x in all_days if ff[k][days[-1]][1] < x <= days[-1]) > MAX_STALE
                       for k in need):
        days.pop()
    px = [px_src[d] for d in days]
    g = lambda k, d: ff[k][d][0]

    raw = {
        "vix":   [g("vix", d) for d in days],
        "term":  [g("vix", d) / g("vix3m", d) for d in days],
        "skew":  [g("skew", d) for d in days],
        "move":  [g("move", d) for d in days],
        "credit": [g("baa10y", d) for d in days],
        "curve": [g("t10y2y", d) for d in days],
    }
    build_features.last_stale = stale
    # 价格结构特征(同样只用过去)
    ma200, dd252, mom63, rv21 = [], [], [], []
    for i in range(len(px)):
        ma200.append(px[i] / (sum(px[i - 199:i + 1]) / 200) - 1 if i >= 199 else None)
        dd252.append(px[i] / max(px[max(0, i - 251):i + 1]) - 1 if i >= 60 else None)
        mom63.append(px[i] / px[i - 63] - 1 if i >= 63 else None)
        if i >= 21:
            rets = [px[j] / px[j - 1] - 1 for j in range(i - 20, i + 1)]
            rv21.append(st.pstdev(rets))
        else:
            rv21.append(None)
    raw["trend"] = ma200
    raw["drawdown"] = dd252
    raw["momentum"] = mom63
    raw["realvol"] = rv21

    ranks = {}
    for k, v in raw.items():
        clean = [x if x is not None else 0.0 for x in v]
        r = rolling_rank(clean)
        for i, x in enumerate(v):
            if x is None:
                r[i] = None
        ranks[k] = r
    feats = [{k: ranks[k][i] for k in ranks if ranks[k][i] is not None} for i in range(len(days))]
    return days, px, feats


def distance(a, b):
    keys = [k for k in a if k in b]
    if len(keys) < 6:
        return None
    return (sum((a[k] - b[k]) ** 2 for k in keys) / len(keys)) ** 0.5


def find_analogs(feats, i_now, max_end, k=K, sep=MIN_SEP):
    """在 [WARMUP, max_end] 里找与 i_now 最像的 k 天; max_end 保证类比日的
    完整 126 日未来窗口不越过调用方允许的边界(回测时用来杜绝前视)。"""
    cand = []
    for j in range(WARMUP, max_end + 1):
        if abs(j - i_now) < 63:      # 排除自身附近, 否则"最像今天的"就是昨天
            continue
        d = distance(feats[i_now], feats[j])
        if d is not None:
            cand.append((d, j))
    cand.sort()
    picked = []
    for d, j in cand:
        if all(abs(j - p) >= sep for _, p in picked):
            picked.append((d, j))
            if len(picked) >= k:
                break
    return picked


def path_quantiles(px, idxs, horizon=HORIZON):
    """按天给出分位。返回 {日: {p10,p25,p50,p75,p90,n}}"""
    out = {}
    for h in range(horizon + 1):
        v = sorted((px[i + h] / px[i] - 1) * 100 for i in idxs if i + h < len(px))
        if len(v) < 5:
            break
        out[h] = {"n": len(v),
                  "p10": round(pctile(v, .10), 2), "p25": round(pctile(v, .25), 2),
                  "p50": round(pctile(v, .50), 2), "p75": round(pctile(v, .75), 2),
                  "p90": round(pctile(v, .90), 2),
                  "pos": round(100 * sum(1 for x in v if x > 0) / len(v), 1)}
    return out


EVAL_HS = (1, 3, 5, 10, 21, 63, 126)


def walk_forward(days, px, feats, step=21):
    """方法自检。评估日只用其之前的数据做推演, 再与真实结果对账。"""
    # 用户要吃短线 → 自检必须覆盖短周期, 否则等于把没验过的短线数字摆给他用
    res = {h: {"hit": 0, "tot": 0, "cov50": 0, "cov80": 0, "z": [],
               "ae_model": [], "ae_naive": [], "base_hit": 0} for h in EVAL_HS}
    evals = 0
    start = WARMUP + 260
    for i in range(start, len(px) - HORIZON - 1, step):
        # 类比日的完整未来窗口必须结束于 i 之前 → j + HORIZON <= i
        max_end = i - HORIZON - 1
        if max_end < WARMUP + 10:
            continue
        an = find_analogs(feats, i, max_end)
        if len(an) < 15:
            continue
        idxs = [j for _, j in an]
        evals += 1
        # 笨基准: 同一批可用历史里的无条件中位
        for h in EVAL_HS:
            if i + h >= len(px):
                continue
            real = (px[i + h] / px[i] - 1) * 100
            v = sorted((px[j + h] / px[j] - 1) * 100 for j in idxs if j + h < len(px))
            if len(v) < 10:
                continue
            med = pctile(v, .5)
            naive = st.median([(px[j + h] / px[j] - 1) * 100
                               for j in range(WARMUP, max_end + 1) if j + h < len(px)])
            r = res[h]
            r["tot"] += 1
            if (med > 0) == (real > 0):
                r["hit"] += 1
            if (naive > 0) == (real > 0):
                r["base_hit"] += 1
            if pctile(v, .25) <= real <= pctile(v, .75):
                r["cov50"] += 1
            if pctile(v, .10) <= real <= pctile(v, .90):
                r["cov80"] += 1
            r["ae_model"].append(abs(med - real))
            r["ae_naive"].append(abs(naive - real))
            # 共形校准用的标准化残差: 实际结果偏离中位数多少个"四分位距"
            iqr = pctile(v, .75) - pctile(v, .25)
            if iqr > 1e-9:
                r["z"].append((real - med) / iqr)
    out = {}
    for h, r in res.items():
        if not r["tot"]:
            continue
        out[h] = {
            "n": r["tot"],
            "dir_hit": round(100 * r["hit"] / r["tot"], 1),
            "dir_hit_naive": round(100 * r["base_hit"] / r["tot"], 1),
            "cov50": round(100 * r["cov50"] / r["tot"], 1),
            "cov80": round(100 * r["cov80"] / r["tot"], 1),
            "mae": round(st.mean(r["ae_model"]), 2),
            "mae_naive": round(st.mean(r["ae_naive"]), 2),
        }
        # "赢"必须有实质幅度: 误差至少低 5%, 且方向命中不低于笨基准。
        # 否则 3.19 vs 3.25 这种 1.8% 的差距会把 verdict 翻成"优于基准" —— 那是噪音不是能力,
        # 而且它会随一次无关的数据改动来回翻, 本身就说明不该当成结论。
        _rel = (out[h]["mae_naive"] - out[h]["mae"]) / out[h]["mae_naive"] if out[h]["mae_naive"] else 0
        out[h]["mae_gain_pct"] = round(100 * _rel, 1)
        out[h]["beats_naive"] = (_rel >= 0.05) and (out[h]["dir_hit"] >= out[h]["dir_hit_naive"])
        # 共形校准系数: 原始分位带覆盖率不达标(实测 25-75 带只盖住约 40%), 说明类比样本
        # 彼此相关、离散度被低估。用历史标准化残差的经验分位反推该有多宽。
        z = sorted(r["z"])
        if len(z) >= 30:
            out[h]["z"] = {q: round(pctile(z, q), 3) for q in (.10, .25, .50, .75, .90)}
    return out, evals


def calibrate(q, skill):
    """把原始分位带按 walk-forward 学到的经验残差重画, 让 50%/80% 带真的覆盖 50%/80%。
    没有校准系数的天数用最近的可用地平线插值(21/63/126 之间线性)。"""
    hs = sorted(h for h in skill if "z" in skill[h])
    if not hs:
        return {}
    def zfor(h, qq):
        if h <= hs[0]:
            return skill[hs[0]]["z"][qq]
        if h >= hs[-1]:
            return skill[hs[-1]]["z"][qq]
        for a, b in zip(hs, hs[1:]):
            if a <= h <= b:
                t = (h - a) / (b - a)
                return skill[a]["z"][qq] * (1 - t) + skill[b]["z"][qq] * t
        return skill[hs[-1]]["z"][qq]
    out = {}
    for h, r in q.items():
        iqr = r["p75"] - r["p25"]
        med = r["p50"]
        out[h] = {qn: round(med + zfor(h, qq) * iqr, 2)
                  for qn, qq in (("p10", .10), ("p25", .25), ("p75", .75), ("p90", .90))}
        out[h]["p50"] = round(med + zfor(h, .50) * iqr, 2)
    return out


def run(S, target, label):
    days, px, feats = build_features(S, target)
    print(f"\n=== {label} ===  {len(days)} 交易日  {days[0]} → {days[-1]}")
    i_now = len(days) - 1
    max_end = len(px) - HORIZON - 1
    an = find_analogs(feats, i_now, max_end)
    if len(an) < 10:
        print("  类比样本不足, 跳过")
        return None
    idxs = [j for _, j in an]
    q = path_quantiles(px, idxs)

    print(f"  最近的 {len(an)} 个类比日 (距离越小越像):")
    for d, j in an[:10]:
        fwd = [(px[j + h] / px[j] - 1) * 100 if j + h < len(px) else None for h in EVAL_HS]
        s = "  ".join(f"{x:+6.1f}%" if x is not None else "   na " for x in fwd)
        print(f"    {days[j]}  d={d:.4f}   +1/3/5/10/21/63/126日 {s}")

    print(f"\n  推演分位 (以 {px[-1]:.2f} 为起点):")
    for h in (1, 3, 5, 10, 21, 63, 126):
        if h in q:
            r = q[h]
            print(f"    +{h:<4}日  p25 {r['p25']:+6.2f}%  中位 {r['p50']:+6.2f}%  "
                  f"p75 {r['p75']:+6.2f}%  |  10-90 [{r['p10']:+.1f}%, {r['p90']:+.1f}%]  正{r['pos']:.0f}%")

    # 笨基准路径: 同一段历史里任意一天出发的无条件分位 —— 画在同一张图上做对照。
    # 类比中位线跟它几乎重合, 正是"没有预测力"最直观的证据, 必须让用户看见。
    naive_idx = list(range(WARMUP, len(px) - HORIZON - 1))
    naive_q = path_quantiles(px, naive_idx)

    print("\n  === 方法自检 (walk-forward, 只用当时可见的数据) ===")
    skill, evals = walk_forward(days, px, feats)
    for h, r in sorted(skill.items()):
        verdict = "✓打得过笨基准" if r["beats_naive"] else "✗打不过笨基准"
        print(f"    +{h:<4}日  评估{r['n']}次  方向命中 {r['dir_hit']}% (笨基准 {r['dir_hit_naive']}%)  "
              f"中位绝对误差 {r['mae']} vs {r['mae_naive']}  {verdict}")
        print(f"            区间覆盖: 25-75带 {r['cov50']}%(理想50)  10-90带 {r['cov80']}%(理想80)")

    return {
        "target": target, "label": label,
        "base": {"last": round(px[-1], 2), "date": days[-1]},
        # 用户 07-30: "这个事件跨度太大了...我要是有机会肯定想吃短线增加资金利用率"
        # → 21/63/126 日对短线毫无用处, 补 1/3/5/10 日, 并给出该类比日之后 20 个交易日
        #   的收盘序列(mini K 线用, series_cache 只有收盘价所以画折线不画蜡烛, 见 E52)。
        "analogs": [{"date": days[j], "dist": round(d, 4),
                     **{f"r{h}": (round((px[j + h] / px[j] - 1) * 100, 2) if j + h < len(px) else None)
                        for h in (1, 3, 5, 10, 21, 63, 126)},
                     "fwd20": [round((px[j + t] / px[j] - 1) * 100, 2)
                               for t in range(0, 21) if j + t < len(px)]}
                    for d, j in an],
        "paths": [[round((px[j + h] / px[j] - 1) * 100, 2)
                   for h in range(HORIZON + 1) if j + h < len(px)] for _, j in an[:8]],
        "quantiles": q,
        "calibrated": calibrate(q, skill),
        "naive": naive_q,
        "features_now": {k: round(v, 3) for k, v in feats[i_now].items()},
        "skill": skill, "skill_evals": evals,
        "verdict": ("no_skill" if all(not v.get("beats_naive") for v in skill.values())
                    else "partial"),
        "k": len(an), "horizon": HORIZON, "pct_win": PCT_WIN,
        "stale": getattr(build_features, "last_stale", {}),
    }


def main():
    S = json.loads(CACHE.read_text())
    S = {k: v for k, v in S.items() if not k.startswith("_")}
    out = {"generated": date.today().isoformat(), "targets": {}}
    for tgt, lab in (("spx", "标普500"), ("btc", "BTC")):
        if tgt not in S or len(S[tgt]) < 1500:
            print(f"跳过 {lab}: 序列不足")
            continue
        r = run(S, tgt, lab)
        if r:
            out["targets"][tgt] = r
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"\n写出 {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
