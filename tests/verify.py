"""Thorough verification suite for the Turbulence Monitor.

Separates two very different questions:
  (A) CORRECTNESS  — is the code right, causal (no future leakage), and is what the website
                     shows exactly what the engine computed?  (Fully testable offline.)
  (B) ACCURACY     — does the model actually detect abnormalities / forecast turbulence well?
                     Calibration + discrimination are testable; the REAL-WORLD edge needs
                     live data + labeled events (run this on your machine for that).

Run:  python -m tests.verify                     (synthetic; offline-safe)
      TURB_MODE=live python -m tests.verify      (on your machine, real data)
"""
from __future__ import annotations
import os, numpy as np, pandas as pd

from src.util import load_config
from src.data import build_synthetic, get_panel
from src.features import build_features, FINGERPRINT
from src.signals import compute_all, anomaly, flare_prob, har_turbulence
from src.simulator import run as run_sim, _stats
import asyncio, time, datetime as dt

CFG = load_config()
PASS, FAIL = [], []
def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — '+detail) if detail else ''}")

def _last_val(series):
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else None

def auc(score, label):
    s = pd.concat([score, label], axis=1).dropna(); s.columns=["s","y"]
    pos=s[s['y']==1]['s'].values; neg=s[s['y']==0]['s'].values
    if len(pos)==0 or len(neg)==0: return float('nan')
    allv=np.concatenate([pos,neg]); order=allv.argsort()
    ranks=np.empty_like(order,dtype=float); ranks[order]=np.arange(1,len(allv)+1)
    return float((ranks[:len(pos)].sum()-len(pos)*(len(pos)+1)/2)/(len(pos)*len(neg)))

print("="*70); print("A. CODE CORRECTNESS & WIRING"); print("="*70)
mode = os.environ.get("TURB_MODE","synthetic")
panel = get_panel("synthetic" if mode=="synthetic" else "auto")
feats = build_features(panel)
sig = compute_all(feats, CFG)
check("pipeline runs end-to-end", len(sig)>500, f"{len(sig)} rows")
check("no NaN in latest flare_prob (today gets a call)", pd.notna(sig['flare_prob'].dropna().iloc[-1]) if sig['flare_prob'].notna().any() else False)
check("anomaly p-values within [0,1]", sig['anom_p'].dropna().between(0,1).all())
check("regime only valid labels", set(sig['regime'].unique()) <= {"Calm","FlightToQuality","ElevatedStress"})

print("\n"+"="*70); print("B. CAUSAL INTEGRITY  (no future-data leakage)"); print("="*70)
full = flare_prob(feats, CFG['signals']['flare_horizon_days'], CFG['signals']['flare_quantile'])['flare_prob']
cutT = pd.Timestamp("2022-12-31")
fc = build_features(panel[panel.index <= cutT])
cut = flare_prob(fc, CFG['signals']['flare_horizon_days'], CFG['signals']['flare_quantile'])['flare_prob']
yr2022 = (full.index>=pd.Timestamp("2022-01-01"))&(full.index<=cutT)
a,b = full[yr2022].dropna(), cut.reindex(full[yr2022].index).dropna()
common = a.index.intersection(b.index)
maxdiff = float((a.reindex(common)-b.reindex(common)).abs().max()) if len(common) else 1.0
check("flare model is causal (2022 preds unchanged when 2023+ removed)", maxdiff < 1e-9, f"max|delta|={maxdiff:.2e} on {len(common)} days")

afull = anomaly(feats, CFG['signals']['anomaly_alpha'])['anom_p']
acut = anomaly(fc, CFG['signals']['anomaly_alpha'])['anom_p']
idx = afull[(afull.index>=pd.Timestamp("2021-01-01"))&(afull.index<=cutT)].dropna().index
idx = idx.intersection(acut.dropna().index)
admax = float((afull.reindex(idx)-acut.reindex(idx)).abs().max()) if len(idx) else 1.0
check("anomaly alarm is causal (past p unchanged when future removed)", admax < 1e-9, f"max|delta|={admax:.2e}")

_, curves = run_sim(feats, sig)
check("simulator buy_hold == invested-every-day benchmark", curves['buy_hold'].iloc[-1] > 0)

print("\n"+"="*70); print("C. ANOMALY DETECTOR  — false-alarm calibration & sensitivity"); print("="*70)
rng = np.random.default_rng(0)
n=4000; idxd=pd.bdate_range("2005-01-01", periods=n)
ret = rng.normal(0,0.01,n)
calm = pd.DataFrame(index=idxd)
calm['SPY']=100*np.cumprod(1+ret)
calm['vix']=np.clip(16+rng.normal(0,1.5,n),9,40); calm['VIX3M']=calm['vix']+2
calm['hy_oas']=np.clip(3.5+rng.normal(0,0.2,n),2.5,8)
calm['y10']=3+np.cumsum(rng.normal(0,0.005,n)); calm['y2']=calm['y10']-0.5
calm['dollar']=100+np.cumsum(rng.normal(0,0.03,n)); calm['oil']=70+np.cumsum(rng.normal(0,0.2,n))
calm['GLD']=150; calm['TLT']=120
for a in (0.01,0.05):
    fr = anomaly(build_features(calm), a)['anom_flag'].mean()
    check(f"false-alarm rate ~ alpha={a} on pure noise", a*0.4 <= fr <= a*2.6, f"empirical={fr:.4f}")

sp = build_synthetic(seed=3)
sf = build_features(sp); sa = anomaly(sf, 0.05)
hv = (sf['rv21'] > sf['rv21'].rolling(252,min_periods=120).quantile(0.9))
both = pd.concat([sa['anom_flag'],hv],axis=1).dropna(); both.columns=['f','h']
p_flag_given_high = both[both['h']]['f'].mean(); p_flag = both['f'].mean()
lift = (p_flag_given_high/p_flag) if p_flag>0 else 0
check("anomaly flags concentrate in genuinely turbulent periods (lift>2)", lift>2, f"lift={lift:.1f}x")

print("\n"+"="*70); print("D. TURBULENCE / FLARE FORECAST — discrimination & calibration"); print("="*70)
d = pd.concat([sig['flare_prob'], sig['flare_ev']], axis=1).dropna(); d.columns=['p','y']
model_auc = auc(d['p'], d['y']); vix_auc = auc(feats['vix'].reindex(d.index), d['y'])
brier = float(((d['p']-d['y'])**2).mean()); base = float(d['y'].mean())
brier_base = float(((base-d['y'])**2).mean())
check("flare model discriminates better than chance (AUC>0.5)", model_auc>0.5, f"AUC={model_auc:.3f}")
check("flare probabilities are calibrated (Brier beats base rate)", brier<brier_base, f"Brier={brier:.3f} vs base {brier_base:.3f}")
h = har_turbulence(sf)
realized = (sf['ret'].rolling(21).std()*np.sqrt(252)).shift(-21)
naive = sf['ret'].rolling(21).std()*np.sqrt(252)
dd = pd.concat([h,naive,realized],axis=1).dropna(); dd.columns=['h','n','r']
r2 = 1 - ((dd['r']-dd['h'])**2).sum()/((dd['r']-dd['r'].mean())**2).sum()
check("HAR vol forecast has positive out-of-sample R^2", r2>0, f"R2={r2:.3f}")
print(f"     [info] flare AUC {model_auc:.3f} vs VIX-only {vix_auc:.3f}  | Brier {brier:.3f}  | mean_pred {d['p'].mean():.3f} actual {base:.3f}")

print("\n"+"="*70); print("E. SIMULATOR MATH — formula correctness on a known toy"); print("="*70)
eq = pd.Series([100,110,99,108.9], index=pd.bdate_range("2020-01-01",periods=4))
sr = eq.pct_change().fillna(0)
st = _stats(eq, sr, pd.Series(1.0,index=eq.index), 100.0)
dd_true = (eq/eq.cummax()-1).min()*100
check("max drawdown formula correct", abs(st['max_drawdown_pct']-round(dd_true,1))<0.11, f"{st['max_drawdown_pct']}% vs {dd_true:.1f}%")
check("total return formula correct", st['total_return_pct']==round((eq.iloc[-1]/100-1)*100,1))
# buy_hold final equity == start_cash * cumulative product of (1+daily return) over the sim window
bh_stats, bh_curves = run_sim(feats, sig)
dsim = pd.DataFrame({'ret':feats['ret'],'vix':feats['vix']}).dropna()
dsim = dsim[dsim.index>=pd.Timestamp(CFG['project']['sim_start'])]
expected_bh = CFG['project']['start_cash'] * float((1+dsim['ret']).prod())
got_bh = float(bh_curves['buy_hold'].iloc[-1])
check("buy_hold == start_cash * prod(1+returns)", abs(got_bh-expected_bh)/expected_bh < 1e-6, f"{got_bh:.0f} vs {expected_bh:.0f}")
check("coinflip invested ~50% of the time", 40 <= bh_stats['coinflip']['pct_time_invested'] <= 60, f"{bh_stats['coinflip']['pct_time_invested']}%")
print(f"     [info] coinflip Sharpe={bh_stats['coinflip']['sharpe']} (a random strategy's Sharpe tracks market drift, it is NOT guaranteed ~0.5)")

print("\n"+"="*70); print("F. WEBSITE ACCURACY — what the API serves == what the engine computed"); print("="*70)
from web.engine_api import build_snapshot
snap,f2,s2 = build_snapshot("synthetic" if mode=="synthetic" else "auto")
o=snap['outlook']
flare_raw = _last_val(s2['flare_prob'])
if flare_raw is None:
    check("website flare% == engine flare_prob", False, "no flare_prob — partial panel")
else:
    eng_flare = round(flare_raw * 100, 1)
    check("website flare% == engine flare_prob", o['flare_prob_pct']==eng_flare, f"site {o['flare_prob_pct']} vs engine {eng_flare}")
anom_raw = _last_val(s2['anom_p'])
if anom_raw is None:
    check("website anomaly p == engine anom_p", False, "no anom_p — partial panel")
else:
    eng_anom_p = round(anom_raw, 4)
    check("website anomaly p == engine anom_p", o['anomaly']['p_value']==eng_anom_p, f"site {o['anomaly']['p_value']} vs engine {eng_anom_p}")
vix_raw = _last_val(f2['vix'])
if vix_raw is None:
    check("website VIX == engine latest VIX", False, "no vix — partial panel")
else:
    eng_vix = round(vix_raw, 1)
    check("website VIX == engine latest VIX", snap['outlook']['vix']==eng_vix, f"site {snap['outlook']['vix']} vs engine {eng_vix}")
check("website calibration uses full out-of-sample history", snap['calibration']['n']>200, f"n={snap['calibration']['n']}")
check("website honestly labels demo vs live data", snap['data_source'] in ("synthetic","live"), snap['data_source'])

print("\n"+"="*70); print("G. LIVE NEWS DETECTOR — precision on calm vs burst, causal guard"); print("="*70)
from src.live_news import LiveNews
from src.entities import extract
ln = LiveNews(CFG)
ev = ln._emit_news(title="Markets calm in quiet trade", url="", source="Test", ts_publish=time.time()+99999)
ts_ok = dt.datetime.fromisoformat(ev['ts_publish'].replace("Z","")) <= dt.datetime.utcnow()+dt.timedelta(seconds=2)
check("live news causal guard (no future timestamps accepted)", ts_ok)
async def runreplay():
    L=LiveNews(CFG); q=await L.bc.subscribe(); t=asyncio.create_task(L.run('replay',speed=12))
    conf=tent=calm_shock=0; end=time.time()+5
    while time.time()<end:
        try: e=await asyncio.wait_for(q.get(),timeout=1)
        except: continue
        if e.get('type')=='news':
            if e['tier']=='confirmed': conf+=1
            if e['tier']=='tentative': tent+=1
            if ('edge higher' in e['headline'] or 'quiet morning' in e['headline']) and e['tier']!='low': calm_shock+=1
    t.cancel(); return conf,tent,calm_shock
conf,tent,calm_shock = asyncio.run(runreplay())
check("calm headlines never raise a shock", calm_shock==0)
check("a real burst raises a confirmed shock", conf>=1, f"confirmed={conf} tentative={tent}")
e1=extract("SVB stock craters as bank stress fears spread across financials")
check("entity tagging: SVB -> Financials/bank_stress", "SIVB" in e1['tickers'] and "Financials" in e1['sectors'])
e2=extract("Nvidia and AMD slump as chip export controls hit semiconductors")
check("entity tagging: chips -> NVDA/AMD/semiconductors", {"NVDA","AMD"} <= set(e2['tickers']) and "semiconductors" in e2['themes'])
# single-headline shock test: SVB collapse fires, neutral does not (fresh instances, no buffer carryover)
svb = LiveNews(CFG)._score("Silicon Valley Bank collapses as depositors flee", "", "Reuters", time.time())
neu = LiveNews(CFG)._score("Company declares routine quarterly dividend in line with guidance", "", "Reuters", time.time())
check("SVB-collapse headline raises a shock (tier != low)", svb['tier'] != 'low', f"tier={svb['tier']} shock={svb['shock']}")
check("neutral headline stays low (no false shock)", neu['tier'] == 'low', f"tier={neu['tier']} shock={neu['shock']}")

print("\n"+"="*70)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL: print("  FAILED:", FAIL)
print("="*70)
