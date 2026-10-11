#!/usr/bin/env python3
"""
threshold_audit — 阈值基准率审计 (2026-07-29)

回答一个此前从没人问过的问题: **这条预警线在历史上到底多久触发一次?**

背景(必须记住的教训): 07-28 建了阈值单一信源、建了三处对账硬校验, 全都在保证
"判灯代码/卡片文案/监控脚本三处数字一致" —— 但没有一条校验问过"这个数字本身对不对"。
结果 SKEW 的 145 线被时代漂移废掉了大半年没人发现(2007-2013 触发率 0.0%, 2024 年 61%),
一条"十年一遇"的线退化成抛硬币, 还在往用户手机上发预警。

本脚本每次构建都跑, 对每条有长历史的线算:
  · 全样本触发率 —— 超过 25% 就不叫预警了, 叫常态
  · 分年代触发率 —— 极差超过 15pp 说明这条线被时代漂移影响
  · 现行线落在自身分布的分位 (全样本 vs 近2年) —— 看它是变松了还是变严了
判定只出报告不自动改阈值: **有些线的绝对含义是真的**(如 10Y-2Y ≤0 是"倒挂"这个
经济事件本身, 2020-2026 触发 33% 是因为那几年真的长期倒挂, 不是阈值错), 需要人判断。
"""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
S=json.loads((ROOT/'data/series_cache.json').read_text())
S={k:v for k,v in S.items() if not k.startswith('_')}
TH=json.loads((ROOT/'data/thresholds.json').read_text())['indicators']

def pct(sv,q):
    i=q*(len(sv)-1); lo,hi=int(i),min(int(i)+1,len(sv)-1)
    return sv[lo]+(sv[hi]-sv[lo])*(i-lo)

# 指标 → (序列函数, 方向)  只列有长历史的
SER={
 'vix_level':      (lambda: S['vix'], 'ge'),
 'vix_term_structure':(lambda: {d:S['vix'][d]/S['vix3m'][d] for d in S['vix'] if d in S['vix3m']}, 'ge'),
 'skew_index':     (lambda: S['skew'], 'ge'),
 'move_index':     (lambda: S['move'], 'ge'),
 'yield_curve_10y2y':(lambda: S['t10y2y'], 'le'),
 'fear_greed_crypto':(lambda: S['crypto_fng'], 'band'),
}
ERAS=[('2007-2013','2007','2014'),('2014-2019','2014','2020'),('2020-2026','2020','2027')]
print(f"[阈值基准率审计]")
print(f"{'指标':<22}{'现行预警线':>10}{'全样本触发率':>12}  分年代触发率")
print("-"*92)
flags=[]
for iid,(fn,kind) in SER.items():
    t=TH[iid]; ser=fn(); days=sorted(ser)
    if kind=='band':
        lo,hi=t['warn_lo'],t['warn_hi']
        hit=lambda v: v<lo or v>hi; line=f"<{lo}/>{hi}"
    elif kind=='ge':
        w=t['warn']; hit=lambda v,w=w: v>=w; line=f">={w}"
    else:
        w=t['warn']; hit=lambda v,w=w: v<=w; line=f"<={w}"
    allr=100*sum(1 for d in days if hit(ser[d]))/len(days)
    era=[]
    for nm,a,b in ERAS:
        sub=[d for d in days if a<=d[:4]<b]
        era.append((nm, 100*sum(1 for d in sub if hit(ser[d]))/len(sub) if sub else None, len(sub)))
    txt="  ".join(f"{nm} {r:5.1f}%" if r is not None else f"{nm}  n/a" for nm,r,_ in era)
    print(f"{iid:<22}{line:>10}{allr:>11.1f}%  {txt}")
    rs=[r for _,r,_ in era if r is not None]
    if rs and max(rs)-min(rs)>=15: flags.append((iid,'年代漂移大', f"最低{min(rs):.1f}% 最高{max(rs):.1f}%"))
    if allr>=25: flags.append((iid,'触发太频繁', f"全样本 {allr:.1f}% 的交易日都在线上"))
    if allr<=0.5: flags.append((iid,'几乎从不触发', f"全样本仅 {allr:.1f}%"))

print("\n=== 现行线落在自身分布的什么位置 (全样本 vs 近2年) ===")
print(f"{'指标':<22}{'现行线':>10}{'全样本分位':>11}{'近2年分位':>11}{'近2年85分位对应值':>18}")
for iid,(fn,kind) in SER.items():
    if kind=='band': continue
    t=TH[iid]; ser=fn(); days=sorted(ser)
    w=t['warn']; sv=sorted(ser.values()); s2=sorted(ser[d] for d in days[-504:])
    q_all=100*sum(1 for x in sv if x<=w)/len(sv)
    q_2y=100*sum(1 for x in s2 if x<=w)/len(s2)
    tgt=pct(s2,0.85)
    print(f"{iid:<22}{w:>10}{q_all:>10.1f}%{q_2y:>10.1f}%{tgt:>18.2f}")

print("\n=== 需要关注 ===")
if flags:
    for i,k,d in flags: print(f"  ⚠ {i:<22}{k:<14}{d}")
else:
    print("  无")
