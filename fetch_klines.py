#!/usr/bin/env python3
"""抓 34 个事件的日K线(事件前~30天+后~63天) → data/klines.json
数据源: Yahoo chart API(实测覆盖 1929 标普/LUNA1/UST/FTT/^N225 全部所需)。"""
import json, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# 事件id → (yahoo符号, 图例标签)。美股大盘统一 ^GSPC(1929/1987 道指数据Yahoo无, 用标普替代并注明)
def sym_for(e):
    if e["market"] == "us_index":
        note = "标普500" if e["date"] >= "1990" else "标普综合指数(道指日线无公开源, 以标普替代)"
        return "^GSPC", note
    if e["market"] == "us_stock":
        m = {"meta": "META", "facebook": "META", "netflix": "NFLX", "nvda": "NVDA",
             "micron": "MU", "hynix": "000660.KS"}
        for k, v in m.items():
            if k in e["id"].lower():
                lbl = {"META": "Meta/Facebook", "NFLX": "Netflix", "NVDA": "NVIDIA",
                       "MU": "美光", "000660.KS": "SK海力士(首尔, 韩元)"}[v]
                return v, lbl
        raise ValueError("unmapped stock " + e["id"])
    # crypto
    if "ust" in e["id"] and "depeg" in e["id"]:
        return "UST-USD", "UST"
    if "luna" in e["id"]:
        return "LUNA1-USD", "LUNA(对数不适用, 线性)"
    if "ftt" in e["id"]:
        return "FTT-USD", "FTT"
    return "BTC-USD", "BTC"

def yahoo_bars(sym, d1, d2):
    p1 = int(datetime.fromisoformat(d1).replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.fromisoformat(d2).replace(tzinfo=timezone.utc).timestamp())
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
         f"?period1={p1}&period2={p2}&interval=1d")
    last = None
    for delay in (0, 3, 8):
        if delay:
            time.sleep(delay)
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20))
            d = r["chart"]["result"][0]
            q = d["indicators"]["quote"][0]
            bars = []
            for ts, o, h, l, c in zip(d.get("timestamp", []), q["open"], q["high"], q["low"], q["close"]):
                if c is None or o is None:
                    continue
                day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                sig = lambda x: float(f"{x:.5g}")
                bars.append([day, sig(o), sig(h), sig(l), sig(c)])
            return bars
        except Exception as e:
            last = e
    raise RuntimeError(f"{sym}: {last}")

def main():
    events = (json.loads((ROOT / "data" / "events_us.json").read_text())
              + json.loads((ROOT / "data" / "events_crypto.json").read_text()))
    out, fails = {}, []
    today = datetime.now(timezone.utc).date()
    for e in events:
        sym, lbl = sym_for(e)
        ev = datetime.fromisoformat(e["date"]).date()
        d1 = (ev - timedelta(days=32)).isoformat()
        d2 = min(ev + timedelta(days=64), today).isoformat()
        try:
            bars = yahoo_bars(sym, d1, d2)
            if len(bars) < 10:
                raise RuntimeError(f"only {len(bars)} bars")
            out[e["id"]] = {"sym": sym, "label": lbl, "event_date": e["date"], "bars": bars,
                            "truncated": (ev + timedelta(days=64)).isoformat() > today.isoformat()}
            print(f"OK  {e['date']} {sym:12s} {len(bars)} bars")
        except Exception as ex:
            fails.append((e["id"], str(ex)))
            print(f"FAIL {e['id']}: {ex}")
        time.sleep(0.6)
    (ROOT / "data" / "klines.json").write_text(json.dumps(out, ensure_ascii=False))
    print(f"\n{len(out)}/{len(events)} saved, {len(fails)} fails", fails or "")

if __name__ == "__main__":
    main()
