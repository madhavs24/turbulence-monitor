"""Stage 5 — Digest. Render the daily report as Markdown + a standalone HTML card.
This is what the autonomous job leaves for you each morning."""
from __future__ import annotations
import datetime as dt
import pandas as pd
from .util import RESULTS

BAND_COLOR = {"Calm": "#2e7d32", "Watch": "#f9a825", "Elevated": "#ef6c00",
              "High": "#c62828", "Unknown": "#757575"}


def render_markdown(outlook, news, sim_stats, score) -> str:
    o = outlook
    lines = [f"# Market Turbulence Digest — {o['as_of']}", ""]
    lines += [f"> {o['headline']}", ""]
    lines += ["## Best-guess outlook (next ~5 trading days)", ""]
    lines += [f"- **Turbulence band:** {o['turbulence_band']}  "
              f"(flare-up probability **{o['flare_prob_pct']}%**)",
              f"- **Expected volatility (annualized):** {o['expected_vol_annual_pct']}%",
              f"- **Regime:** {o['regime']}",
              f"- **Anomaly alarm:** {'TRIGGERED' if o['anomaly']['flagged'] else 'quiet'} "
              f"(p={o['anomaly']['p_value']})",
              f"- **VIX:** {o['vix']} ({'+' if (o['vix_vs_1y_median'] or 0)>=0 else ''}"
              f"{o['vix_vs_1y_median']} vs 1y median)",
              f"- **Signal confidence:** {o['confidence']}", ""]
    lines += ["## News check (real-time narrative)", ""]
    if news.get("n", 0) == 0:
        lines += ["- No live news available this run.", ""]
    else:
        lines += [f"- **Narrative:** {news['narrative']}  "
                  f"(mean stress score {news['mean_score']}, "
                  f"{int(news['stress_share']*100)}% of {news['n']} headlines negative)",
                  f"- **Agreement with signals:** {o['news_agreement']}"]
        if news.get("injection_flags"):
            lines += [f"- ⚠️ {news['injection_flags']} headline(s) quarantined "
                      f"(suspicious text treated as data, never obeyed)"]
        if news.get("top_negative"):
            lines += ["- Most negative headlines:"]
            lines += [f"    - {t}" for t in news["top_negative"]]
        lines += [""]
    if o["news_agreement"] == "diverges":
        lines += ["> ⚠️ **Signals and news disagree.** Treat the read as lower-confidence.", ""]
    lines += ["## Paper-trading scoreboard (demo $, since 2012)", "",
              "| Strategy | Final | Return | Max drawdown | Sharpe | % invested |",
              "|---|---|---|---|---|---|"]
    for s, v in sim_stats.items():
        lines += [f"| {s} | ${v['final_value']:,} | {v['total_return_pct']}% | "
                  f"{v['max_drawdown_pct']}% | {v['sharpe']} | {v['pct_time_invested']}% |"]
    lines += ["", "*Honest lesson: signal strategies cut drawdown (pain), they do not beat "
              "buy-and-hold on return. This is a risk monitor, not a money machine.*", ""]
    if score.get("scored", 0) > 0:
        lines += ["## Self-score (did past calls hold up?)", "",
                  f"- Scored {score['scored']} past 'quiet' calls: "
                  f"{score['quiet_correct']} correct, {score['quiet_missed_flares']} missed a flare.", ""]
    lines += ["---",
              "*Educational only. Detects turbulence/stress, NOT price direction. "
              "No real money, ever. Not investment advice.*"]
    return "\n".join(lines)


def render_html(outlook, news, sim_stats) -> str:
    o = outlook; c = BAND_COLOR.get(o["turbulence_band"], "#555")
    rows = "".join(
        f"<tr><td>{s}</td><td>${v['final_value']:,}</td><td>{v['total_return_pct']}%</td>"
        f"<td>{v['max_drawdown_pct']}%</td><td>{v['sharpe']}</td></tr>"
        for s, v in sim_stats.items())
    news_line = ("No live news this run." if news.get("n", 0) == 0 else
                 f"Narrative: <b>{news['narrative']}</b> · agreement: "
                 f"<b>{o['news_agreement']}</b> ({news['n']} headlines)")
    return f"""<!doctype html><meta charset=utf-8>
<title>Turbulence Digest {o['as_of']}</title>
<style>body{{font-family:system-ui,Segoe UI,Arial;max-width:680px;margin:24px auto;
color:#222;padding:0 16px}}.band{{background:{c};color:#fff;padding:14px 18px;border-radius:10px}}
.band h1{{margin:0;font-size:20px}}.k{{color:#666}}table{{border-collapse:collapse;width:100%;
margin-top:8px}}td,th{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;font-size:14px}}
.card{{border:1px solid #eee;border-radius:10px;padding:14px 18px;margin-top:14px}}
small{{color:#888}}</style>
<div class=band><h1>{o['turbulence_band']} — turbulence outlook</h1>
<div>{o['headline']}</div></div>
<div class=card><b>Best guess (next ~5 days)</b><br>
Flare-up probability <b>{o['flare_prob_pct']}%</b> · expected vol {o['expected_vol_annual_pct']}% ·
regime {o['regime']} · VIX {o['vix']} ·
anomaly {'TRIGGERED' if o['anomaly']['flagged'] else 'quiet'} ·
confidence {o['confidence']}</div>
<div class=card><b>News check</b><br>{news_line}</div>
<div class=card><b>Paper-trading (demo $, since 2012)</b>
<table><tr><th>Strategy</th><th>Final</th><th>Return</th><th>Max DD</th><th>Sharpe</th></tr>
{rows}</table>
<small>Signal strategies cut drawdown, not beat buy-&-hold. Risk monitor, not a money machine.</small></div>
<p><small>As of {o['as_of']}. Educational only · detects turbulence not direction · no real money · not advice.</small></p>"""


def write_digest(outlook, news, sim_stats, score) -> dict:
    md = render_markdown(outlook, news, sim_stats, score)
    html = render_html(outlook, news, sim_stats)
    d = outlook["as_of"]
    md_p = RESULTS / f"digest_{d}.md"; html_p = RESULTS / f"digest_{d}.html"
    (RESULTS / "digest_latest.md").write_text(md, encoding="utf-8"); md_p.write_text(md, encoding="utf-8")
    (RESULTS / "digest_latest.html").write_text(html, encoding="utf-8"); html_p.write_text(html, encoding="utf-8")
    return {"md": str(md_p), "html": str(html_p)}
