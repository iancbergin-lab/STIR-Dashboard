# Capital Flows Research -- STIR Replication Playbook
# Single-file implementation: A1 through A6.
#
# Data sources
# ============
# EFFR / SOFR : NY Fed public CSV (markets.newyorkfed.org)
#               Falls back to embedded 2026-05 real data when network unavailable.
# FOMC dates  : Hard-coded from federalreserve.gov (refresh annually)
# Futures strip: Databento (GLBX.MDP3, ohlcv-1d schema)
#                Set env var DATABENTO_API_KEY to enable.
#                Falls back to synthetic mock when key is absent.

# -- A1 IMPORTS, PALETTE, SCHEMAS ---------------------------------------------
from __future__ import annotations

import io
import os
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests

CFR = {
    "bg":        "#000000",
    "panel":     "#080808",
    "rule":      "#3D2510",
    "orange":    "#FE7C04",
    "orangeHot": "#FF9533",
    "orangeDim": "#5A2C00",
    "text":      "#D0D0D0",
    "green":     "#00E676",
    "red":       "#FF1744",
}

_CME_MONTH_CODES = {
    1: "F", 2: "G",  3: "H",  4: "J",  5: "K",  6: "M",
    7: "N", 8: "Q",  9: "U", 10: "V", 11: "X", 12: "Z",
}


def _cme_symbol(root: str, expiry: date) -> str:
    return f"{root}{_CME_MONTH_CODES[expiry.month]}{expiry.year % 10}"


@dataclass
class Contract:
    symbol: str
    root:   str
    expiry: date
    settle: float


def to_strip(contracts: list[Contract]) -> pd.DataFrame:
    return pd.DataFrame([c.__dict__ for c in contracts])


# -- A2 LOADERS ---------------------------------------------------------------

_EFFR_EMBEDDED = """Effective Date,Rate (%)
05/28/2026,3.62
05/27/2026,3.62
05/26/2026,3.62
05/22/2026,3.62
05/21/2026,3.62
05/20/2026,3.62
05/19/2026,3.62
05/18/2026,3.63
05/15/2026,3.63
05/14/2026,3.63
05/13/2026,3.63
05/12/2026,3.63
05/11/2026,3.63
05/08/2026,3.63
05/07/2026,3.63
05/06/2026,3.64
05/05/2026,3.64
05/04/2026,3.64
05/01/2026,3.64"""

_SOFR_EMBEDDED = """Effective Date,Rate (%)
05/28/2026,3.62
05/27/2026,3.63
05/26/2026,3.63
05/22/2026,3.55
05/21/2026,3.51
05/20/2026,3.50
05/19/2026,3.51
05/18/2026,3.53
05/15/2026,3.55
05/14/2026,3.56
05/13/2026,3.59
05/12/2026,3.60
05/11/2026,3.60
05/08/2026,3.60
05/07/2026,3.60
05/06/2026,3.61
05/05/2026,3.62
05/04/2026,3.63
05/01/2026,3.64"""


def _parse_nyfed_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    # Prefer exact "Rate (%)" match; fall back to first column containing both
    # "rate" and "%" (avoids "Rate Type" which also contains "rate").
    rate_col = next(
        (c for c in df.columns if c.strip().lower() == "rate (%)"),
        next((c for c in df.columns if "%" in c and "rate" in c.lower()), None),
    )
    if rate_col is None:
        raise ValueError(f"No rate column found. Columns: {list(df.columns)}")
    date_col = next(c for c in df.columns if "date" in c.lower())
    s = pd.to_numeric(df[rate_col], errors="coerce")
    s.index = pd.to_datetime(df[date_col])
    return s.sort_index().dropna()


def load_ref_rates(lookback_days: int = 90) -> pd.DataFrame:
    """
    Load EFFR + SOFR from the NY Fed public CSV endpoint.
    URL: https://markets.newyorkfed.org/read
         ?productCode=50&startDt=YYYY-MM-DD&endDt=YYYY-MM-DD
         &eventCodes=500 (EFFR) or 520 (SOFR) &format=csv
    Falls back to embedded May-2026 real data when unreachable.
    """
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=lookback_days + 40)
    base_url = "https://markets.newyorkfed.org/read"

    def _fetch_live(event_code: int):
        try:
            r = requests.get(base_url, params=dict(
                productCode=50,
                startDt=start_dt.isoformat(),
                endDt=end_dt.isoformat(),
                eventCodes=event_code,
                format="csv",
            ), timeout=10)
            r.raise_for_status()
            return _parse_nyfed_csv(r.text).tail(lookback_days)
        except Exception:
            return None

    effr_live = _fetch_live(500)
    sofr_live = _fetch_live(520)

    if effr_live is None:
        print("  (NY Fed unreachable -- using embedded real data from 2026-05-28)")
    effr = effr_live if effr_live is not None else _parse_nyfed_csv(_EFFR_EMBEDDED)
    sofr = sofr_live if sofr_live is not None else _parse_nyfed_csv(_SOFR_EMBEDDED)

    idx = effr.index.union(sofr.index)
    return pd.DataFrame({"effr": effr.reindex(idx), "sofr": sofr.reindex(idx)}).ffill()


def load_fomc_dates() -> list[date]:
    """FOMC meeting end-dates through end-2026. Source: federalreserve.gov"""
    return [
        date(2025,  1, 29), date(2025,  3, 19), date(2025,  5,  7),
        date(2025,  6, 18), date(2025,  7, 30), date(2025,  9, 17),
        date(2025, 10, 29), date(2025, 12, 10),
        date(2026,  1, 28), date(2026,  3, 18), date(2026,  4, 29),
        date(2026,  6, 17), date(2026,  7, 29), date(2026,  9, 16),
        date(2026, 10, 28), date(2026, 12,  9),
    ]


def load_strip_databento(api_key: str) -> pd.DataFrame:
    """
    Real loader: fetch SR3 + ZQ daily settlement prices from Databento.

    Sign up at databento.com -- new accounts get $125 in free credits.
    pip install databento

    Dataset : GLBX.MDP3  (CME Globex -- covers CBOT, CME, NYMEX, COMEX)
    Schema  : ohlcv-1d   (daily bars; close == settlement price for futures)
    stype_in: parent     (returns every active expiration under the root)

    Prices are stored as fixed-point integers scaled by 1e9.
    e.g. 96420000000 means IMM price 96.42 => implied rate 3.58%.
    """
    try:
        import databento as db
    except ImportError:
        raise ImportError("Run: pip install databento")

    _MONTH_FROM_CODE = {v: k for k, v in _CME_MONTH_CODES.items()}

    def _parse_expiry(sym: str) -> date | None:
        """SR3M6 -> date(2026, 6, 30),  ZQK6 -> date(2026, 5, 31)"""
        try:
            if sym.startswith("SR3"):
                code, yr_ch = sym[3], sym[4]
            elif sym.startswith("ZQ"):
                code, yr_ch = sym[2], sym[3]
            else:
                return None
            month    = _MONTH_FROM_CODE[code]
            yr_digit = int(yr_ch)
            today    = date.today()
            decade   = (today.year // 10) * 10
            year     = decade + yr_digit
            if year < today.year - 1:
                year += 10
            return date(year, month, monthrange(year, month)[1])
        except Exception:
            return None

    client = db.Historical(key=api_key)
    end    = date.today()
    start  = end - timedelta(days=7)   # buffer for weekends / holidays

    contracts: list[Contract] = []
    today = date.today()

    for root in ("SR3", "ZQ"):
        try:
            data = client.timeseries.get_range(
                dataset="GLBX.MDP3",
                symbols=[f"{root}.FUT"],
                schema="ohlcv-1d",
                start=start.isoformat(),
                end=end.isoformat(),
                stype_in="parent",
            )
            df = data.to_df()
        except Exception as exc:
            print(f"  Databento fetch failed for {root}: {exc}")
            continue

        if df.empty:
            continue

        # Identify symbol column (varies slightly across client versions)
        sym_col = next((c for c in ("symbol", "raw_symbol") if c in df.columns), None)
        if sym_col is None:
            print(f"  No symbol column found for {root}. Columns: {list(df.columns)}")
            continue

        # Take the most recent bar per contract; drop spreads/butterflies
        latest = df.sort_index().groupby(sym_col).last().reset_index()
        latest = latest[~latest[sym_col].str.contains(r"[:\s\-]", regex=True)]

        for _, row in latest.iterrows():
            sym    = str(row[sym_col])
            expiry = _parse_expiry(sym)
            if expiry is None or expiry < today:
                continue
            # to_df() already converts Databento fixed-point to decimal prices
            settle = float(row.get("close", 0))
            if settle <= 0:
                continue
            contracts.append(Contract(symbol=sym, root=root, expiry=expiry, settle=settle))

    if not contracts:
        raise RuntimeError(
            "Databento returned no contracts. "
            "Check your API key, account credits, and date range."
        )

    return to_strip(contracts).sort_values(["root", "expiry"]).reset_index(drop=True)


def make_mock_strip(today: date, ocr: float) -> pd.DataFrame:
    """
    Synthetic strip seeded from the real OCR -- used when no API key is set.
    Replace this with load_strip_databento() for live data.
    """
    np.random.seed(42)
    contracts: list[Contract] = []

    # SOFR 3-month futures -- 8 quarterly contracts (~2 years)
    sr3_rates = [max(ocr - i * 0.11, ocr - 0.90) for i in range(8)]
    for i in (5, 6, 7):
        sr3_rates[i] += (i - 4) * 0.07

    for i, r in enumerate(sr3_rates):
        raw_m = today.month + i * 3
        y  = today.year + (raw_m - 1) // 12
        m  = ((raw_m - 1) % 12) + 1
        qm = next(q for q in (3, 6, 9, 12) if q >= m) if m not in (3, 6, 9, 12) else m
        exp = date(y, qm, monthrange(y, qm)[1])
        contracts.append(Contract(_cme_symbol("SR3", exp), "SR3", exp, round(100.0 - r, 4)))

    # Fed Funds 30-day futures -- 18 monthly contracts
    ff_rates = list(
        np.linspace(ocr, ocr - 0.75, 18) + np.random.normal(0, 0.008, 18)
    )
    for i, r in enumerate(ff_rates):
        raw_m = today.month + i
        y   = today.year + (raw_m - 1) // 12
        m   = ((raw_m - 1) % 12) + 1
        exp = date(y, m, monthrange(y, m)[1])
        contracts.append(Contract(_cme_symbol("ZQ", exp), "ZQ", exp, round(100.0 - r, 4)))

    return to_strip(contracts).sort_values(["root", "expiry"]).reset_index(drop=True)


# -- A3 IMPLIED RATE, TERMINAL, STRIP VIEW ------------------------------------

def implied_rate(settle: float) -> float:
    return 100.0 - settle


def add_implied(strip: pd.DataFrame, ocr: float) -> pd.DataFrame:
    out = strip.copy()
    out["implied_rate"] = 100.0 - out["settle"]
    out["vs_ocr_bp"]    = (out["implied_rate"] - ocr) * 100.0
    return out


def find_terminal(strip_view: pd.DataFrame, ocr: float) -> pd.Series:
    active = strip_view[strip_view["settle"] > 0].reset_index(drop=True)
    if active.empty:
        return strip_view.iloc[0]
    front  = active.iloc[0]
    hiking = front["implied_rate"] >= ocr
    best   = front
    for _, row in active.iloc[1:].iterrows():
        if hiking and row["implied_rate"] >= best["implied_rate"]:
            best = row
        elif not hiking and row["implied_rate"] <= best["implied_rate"]:
            best = row
        else:
            break
    return best


def plot_strip(strip_view: pd.DataFrame, ocr: float, title: str) -> go.Figure:
    term   = find_terminal(strip_view, ocr)
    colors = [
        CFR["orangeHot"] if s == term["symbol"] else CFR["orangeDim"]
        for s in strip_view["symbol"]
    ]
    fig = go.Figure(go.Bar(
        x=strip_view["symbol"],
        y=strip_view["implied_rate"],
        marker_color=colors,
        marker_line_color="#9A4A02",
        hovertemplate="%{x}<br>%{y:.3f}%<extra></extra>",
    ))
    fig.add_hline(
        y=ocr,
        line_dash="dash",
        line_color=CFR["orange"],
        annotation_text="EFFECTIVE FFR",
        annotation_position="right",
        annotation_font=dict(color=CFR["orange"], family="Segoe UI"),
    )
    fig.update_layout(
        title=dict(text=title, font=dict(color=CFR["orange"], family="Bahnschrift", size=18)),
        template="plotly_dark",
        paper_bgcolor=CFR["bg"],
        plot_bgcolor="#050505",
        font=dict(family="Segoe UI", color=CFR["text"]),
        yaxis_title="Implied rate (%)",
        xaxis_title=None,
        margin=dict(l=60, r=20, t=60, b=40),
        height=420,
    )
    return fig


# -- A4 MEETING-PATH MATH, PROBABILITIES --------------------------------------

def post_meeting_rate(
    contract_rate: float,
    prev_rate: float,
    meeting_day: int,
    days_in_month: int,
) -> float:
    days_after = days_in_month - meeting_day + 1
    if days_after <= 0:
        return contract_rate
    return (contract_rate * days_in_month - (meeting_day - 1) * prev_rate) / days_after


def build_meeting_path(
    zq_strip: pd.DataFrame,
    effr_today: float,
    fomc_dates: list[date],
) -> pd.DataFrame:
    zq_by_month = {
        (r["expiry"].year, r["expiry"].month): r["implied_rate"]
        for _, r in zq_strip.iterrows()
    }
    fomc_keys = {(d.year, d.month) for d in fomc_dates}
    today  = date.today()
    future = [d for d in fomc_dates if d >= today]

    prev = effr_today
    rows: list[dict] = []
    for d in future:
        rate = zq_by_month.get((d.year, d.month))
        if rate is None:
            continue
        N  = monthrange(d.year, d.month)[1]
        ny = d.year + (1 if d.month == 12 else 0)
        nm = d.month % 12 + 1
        next_rate        = zq_by_month.get((ny, nm))
        next_has_meeting = (ny, nm) in fomc_keys
        if next_rate is not None and not next_has_meeting:
            post = next_rate
        else:
            post = post_meeting_rate(rate, prev, d.day, N)
        rows.append({
            "meeting":   d,
            "post_rate": post,
            "cum_cuts":  (effr_today - post) / 0.25,
        })
        prev = post
    return pd.DataFrame(rows)


def meeting_probs(post_rate: float, effr: float) -> dict[str, float]:
    raw   = (effr - post_rate) / 0.25
    lower = int(np.floor(raw))
    frac  = raw - lower
    mass: dict[int, float] = {lower: 1 - frac}
    if frac > 0.001:
        mass[lower + 1] = frac
    return {
        "hold":   100 * mass.get( 0, 0.0),
        "cut25":  100 * mass.get( 1, 0.0),
        "cut50":  100 * mass.get( 2, 0.0),
        "cut75":  100 * mass.get( 3, 0.0),
        "hike25": 100 * mass.get(-1, 0.0),
    }


# -- A5 SPREAD MATRIX, MEETING-PATH PLOT, CB LVL OVERLAY ----------------------

def spread_matrix(
    strip_view: pd.DataFrame,
    ocr: float,
    horizons_m: tuple[int, ...] = (3, 6, 9, 12),
) -> pd.DataFrame:
    if strip_view.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    for _, row in strip_view.iterrows():
        row_mo = row["expiry"].month + 12 * row["expiry"].year
        spreads: dict[str, float] = {}
        for h in horizons_m:
            target  = row_mo + h
            forward = strip_view[
                strip_view["expiry"].apply(lambda d: d.month + 12 * d.year >= target)
            ]
            if not forward.empty:
                spreads[f"+{h}M"] = round(
                    (forward.iloc[0]["implied_rate"] - row["implied_rate"]) * 100
                )
            else:
                spreads[f"+{h}M"] = float("nan")
        rows.append({"contract": row["symbol"], **spreads})
    return pd.DataFrame(rows).set_index("contract")


def _base_meeting_fig(path: pd.DataFrame, effr: float) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=path["meeting"],
        y=path["post_rate"],
        mode="lines+markers",
        line=dict(color=CFR["orangeHot"], width=2.4, shape="hv"),
        marker=dict(color=CFR["bg"],
                    line=dict(color=CFR["orangeHot"], width=1.5),
                    size=8),
    ))
    fig.add_hline(
        y=effr,
        line_dash="dash",
        line_color=CFR["orange"],
        annotation_text="EFFECTIVE FFR",
        annotation_position="right",
        annotation_font=dict(color=CFR["orange"]),
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=CFR["bg"],
        plot_bgcolor="#050505",
        font=dict(family="Segoe UI", color=CFR["text"]),
        yaxis_title="Implied post-meeting rate (%)",
        margin=dict(l=60, r=40, t=60, b=40),
        height=420,
    )
    return fig


def plot_meeting_path(path: pd.DataFrame, effr: float) -> go.Figure:
    fig = _base_meeting_fig(path, effr)
    fig.update_layout(title=dict(
        text="MEETINGS -- IMPLIED POST-MEETING RATE PATH",
        font=dict(color=CFR["orange"], family="Bahnschrift", size=18),
    ))
    return fig


def cb_levels(effr: float, band_bp: int = 100, step_bp: int = 25) -> list[float]:
    settle = round(effr / 0.25) * 0.25
    n      = band_bp // step_bp
    return [settle + (i - n) * (step_bp / 100.0) for i in range(2 * n + 1)]


def plot_cb_lvl(path: pd.DataFrame, effr: float) -> go.Figure:
    fig    = _base_meeting_fig(path, effr)
    settle = round(effr / 0.25) * 0.25
    fig.update_layout(title=dict(
        text="MEETINGS -- CB LVL POLICY RAILS",
        font=dict(color=CFR["orange"], family="Bahnschrift", size=18),
    ))
    for lv in cb_levels(effr, band_bp=150):
        is_settle = abs(lv - settle) < 0.01
        fig.add_hline(
            y=lv,
            line_color=CFR["orange"]   if is_settle else CFR["orangeDim"],
            line_dash="solid"          if is_settle else "dot",
            line_width=1.4             if is_settle else 0.6,
            annotation_text=f"{lv:.2f}% SETTLE" if is_settle else f"{lv:.2f}%",
            annotation_position="right",
            annotation_font=dict(
                color=CFR["orange"] if is_settle else CFR["orangeDim"],
                size=9,
                family="IBM Plex Mono",
            ),
        )
    return fig


# -- A6 END-TO-END DRIVER -----------------------------------------------------
if __name__ == "__main__":
    TODAY = date.today()

    # 1. Reference rates + FOMC calendar
    print("Fetching EFFR + SOFR from NY Fed ...")
    ref_rates  = load_ref_rates()
    fomc_dates = load_fomc_dates()

    # 2. Anchor rates
    OCR      = float(ref_rates["effr"].iloc[-1])
    SOFR_now = float(ref_rates["sofr"].iloc[-1])
    basis_bp = (SOFR_now - OCR) * 100
    print(f"OCR (EFFR) today : {OCR:.4f}%")
    print(f"SOFR spot        : {SOFR_now:.4f}%  (basis {basis_bp:+.1f} bp)")

    # 3. Futures strip
    #    Set env var DATABENTO_API_KEY to load real CME settlement prices.
    #    Falls back to synthetic mock when the key is absent.
    dbn_key = os.environ.get("DATABENTO_API_KEY", "")
    if dbn_key:
        print("Loading SR3 + ZQ settlements from Databento ...")
        strip = load_strip_databento(dbn_key)
    else:
        print("DATABENTO_API_KEY not set -- using synthetic mock strip.")
        print("  (export DATABENTO_API_KEY=<your_key> to use real data)")
        strip = make_mock_strip(TODAY, OCR)

    # 4. Decorate + split by product
    strip      = add_implied(strip, OCR)
    sofr_strip = strip[strip["root"] == "SR3"].reset_index(drop=True)
    ff_strip   = strip[strip["root"] == "ZQ"].reset_index(drop=True)

    # 5. PRODUCTS tab -- strip charts
    fig_sofr = plot_strip(sofr_strip, OCR, "PRODUCTS -- SOFR (SR3) STRIP")
    fig_ff   = plot_strip(ff_strip,   OCR, "PRODUCTS -- FED FUNDS (ZQ) STRIP")

    # 6. MEETINGS -- meeting-path
    path = build_meeting_path(ff_strip, OCR, fomc_dates)

    # 7. FedWatch-style probability table
    probs_df = pd.DataFrame(
        [meeting_probs(r, OCR) for r in path["post_rate"]],
        index=[d.isoformat() for d in path["meeting"]],
    ).round(1)
    print("\n-- FedWatch-style probabilities (%) --")
    print(probs_df.to_string())

    # 8. Calendar spread matrix
    print("\n-- Calendar spread matrix (ZQ, bp) --")
    print(spread_matrix(ff_strip, OCR).to_string())

    # 9. Remaining figures
    fig_path  = plot_meeting_path(path, OCR)
    fig_cblvl = plot_cb_lvl(path, OCR)

    # 10. Build learning-guide HTML (5th tab -- static, no figure)
    LEARN_HTML = f"""
<div class="learn">
  <h2>How to Read This Dashboard</h2>
  <p class="subtitle">A plain-English guide to STIR futures and what each chart is telling you.</p>

  <div class="section">
    <h3>Background &mdash; What Are STIR Futures?</h3>
    <p><strong>STIR</strong> stands for <em>Short-Term Interest Rate</em>. STIR futures are
    exchange-traded contracts whose price is determined by where the market expects a benchmark
    overnight rate to be at some future point in time. They are the most liquid real-time
    indicator of central-bank policy expectations &mdash; far more granular than surveys or
    analyst forecasts.</p>
    <p>This dashboard tracks two products:</p>
    <table>
      <thead><tr><th>Ticker</th><th>Product</th><th>Reference rate</th><th>Contract size</th></tr></thead>
      <tbody>
        <tr><td>SR3</td><td>CME SOFR 3-Month</td><td>3-month compounded SOFR</td><td>$2,500 per 0.01 DV01</td></tr>
        <tr><td>ZQ</td><td>CME 30-Day Fed Funds</td><td>Monthly average EFFR</td><td>$4,167 per 0.01 DV01</td></tr>
      </tbody>
    </table>
  </div>

  <div class="section">
    <h3>The IMM Price Convention</h3>
    <p>Futures prices are quoted as <strong>100 minus the implied rate</strong>. So a price of
    <code>96.38</code> means the market implies a rate of <code>100 &minus; 96.38 = 3.62%</code>.
    This means:</p>
    <ul>
      <li>Prices <strong>rise</strong> when the market expects <strong>rate cuts</strong>.</li>
      <li>Prices <strong>fall</strong> when the market expects <strong>rate hikes</strong>.</li>
    </ul>
    <p>The dashed orange line on every strip chart shows the current Effective Federal Funds Rate
    (EFFR). Bars above the line imply cuts are priced in; bars below imply hikes.</p>
  </div>

  <div class="section">
    <h3>Tab 1 &mdash; SOFR Strip (SR3)</h3>
    <p>Each bar represents one quarterly SR3 contract expiring on the IMM date of that month
    (third Wednesday). The height of the bar is the <strong>implied 3-month SOFR rate</strong>
    for that expiry window.</p>
    <p><strong>The bright orange bar</strong> is the <em>terminal contract</em> &mdash; the
    furthest point along the strip where the market expects rates to peak or trough before
    reversing. Contracts beyond it start drifting back toward neutral.</p>
    <p><strong>What to look for:</strong></p>
    <ul>
      <li>A strip sloping <em>downward</em> = market pricing in rate cuts.</li>
      <li>A strip sloping <em>upward</em> = hikes expected.</li>
      <li>A flat strip = no change expected, policy on hold.</li>
      <li>The <em>steepness</em> of the slope reflects conviction &mdash; a steep curve means
      large, rapid moves are priced; a gentle slope means a gradual path.</li>
    </ul>
  </div>

  <div class="section">
    <h3>Tab 2 &mdash; Fed Funds Strip (ZQ)</h3>
    <p>ZQ contracts settle against the <strong>monthly average EFFR</strong>, so each bar
    represents the market's expectation for the average overnight rate across that calendar
    month. Because FOMC decisions are instantaneous but ZQ settles on the monthly average,
    you can arithmetically back out exactly what rate change is priced in for each meeting
    (see Meeting Path tab).</p>
    <p>ZQ is more granular than SR3 for near-term meeting analysis because it has monthly
    expiries rather than quarterly.</p>
  </div>

  <div class="section">
    <h3>Tab 3 &mdash; Meeting Path</h3>
    <p>This chart extracts the <strong>implied post-meeting rate</strong> from each ZQ contract
    that covers an FOMC meeting month. The formula is:</p>
    <pre>post_rate = (contract_rate × days_in_month − pre_meeting_days × prev_rate)
             ÷ post_meeting_days</pre>
    <p>This isolates the overnight rate that must prevail <em>after</em> the meeting to produce
    the observed futures price, given the rate that prevailed before the meeting.</p>
    <p><strong>What to look for:</strong></p>
    <ul>
      <li>Each dot is one FOMC meeting. The step-function line shows the expected policy path.</li>
      <li>A step <em>down</em> at a meeting = a cut is priced for that date.</li>
      <li>A step <em>up</em> = a hike.</li>
      <li>A flat line between meetings = no action expected.</li>
      <li>The gap between dots and the orange EFFR line tells you how many total cuts or hikes
      are priced by that meeting.</li>
    </ul>
  </div>

  <div class="section">
    <h3>Tab 4 &mdash; CB Level Rails</h3>
    <p>Central banks move rates in <strong>25 bp increments</strong>. The dotted horizontal
    lines on this chart (the "rails") mark every 25 bp level within 150 bp of the current
    settled rate. The solid bright orange line is the <em>current settled rate</em> (rounded
    to the nearest 25 bp).</p>
    <p>This overlay turns the meeting path into a probability map: the closer a meeting's dot
    is to a rail, the higher the probability of that outcome. A dot exactly on the rail below
    = 100% one-cut. A dot halfway between = 50/50 between hold and cut.</p>
    <p><strong>FedWatch probabilities</strong> (printed in the terminal) are derived directly
    from this geometry &mdash; they show the probability mass assigned to hold, &minus;25 bp,
    &minus;50 bp, &minus;75 bp, and +25 bp at each meeting.</p>
  </div>

  <div class="section">
    <h3>The EFFR / SOFR Basis</h3>
    <p>The header shows EFFR, SOFR, and their basis (SOFR &minus; EFFR, in basis points).
    In normal conditions SOFR trades a few bp <em>below</em> EFFR because SOFR is
    collateralised (repo-backed) while EFFR reflects uncollateralised interbank lending.</p>
    <ul>
      <li><strong>Basis near zero</strong> = normal, plentiful reserves, no stress.</li>
      <li><strong>Basis sharply negative</strong> = SOFR is cheap, repo markets are flush.</li>
      <li><strong>Basis turning positive</strong> = unusual stress signal; SOFR pricing above
      EFFR can indicate collateral scarcity or quarter-end pressure.</li>
    </ul>
  </div>

  <div class="section">
    <h3>Calendar Spreads (printed in terminal)</h3>
    <p>The spread matrix shows the difference in implied rates between a ZQ contract and the
    contract N months forward (+3M, +6M, +9M, +12M), expressed in <strong>basis points</strong>.
    A positive spread means the forward rate is higher than the near rate &mdash; the market
    expects rates to <em>rise</em> over that horizon. Negative spreads imply expected cuts.</p>
    <p>Watch for <em>curve inversions</em>: when near-dated spreads are more negative than
    far-dated ones, the market believes cuts will happen quickly and then stabilise &mdash;
    a classic easing cycle shape.</p>
  </div>

  <div class="section">
    <h3>Data Sources &amp; Refresh</h3>
    <table>
      <thead><tr><th>Data</th><th>Source</th><th>Frequency</th></tr></thead>
      <tbody>
        <tr><td>EFFR &amp; SOFR fixings</td><td>NY Fed public CSV API</td><td>Daily (T+1 business day)</td></tr>
        <tr><td>FOMC meeting calendar</td><td>Federal Reserve (hard-coded)</td><td>Annual refresh</td></tr>
        <tr><td>SR3 &amp; ZQ settlements</td><td>Databento / CME Globex</td><td>Daily close</td></tr>
        <tr><td>This page</td><td>Netlify build trigger</td><td>Hourly on weekdays</td></tr>
      </tbody>
    </table>
  </div>
</div>
"""

    # 11. Save combined single-file dashboard with tabs → public/index.html
    #     Netlify serves the public/ directory as the site root.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    public_dir = os.path.join(script_dir, "public")
    os.makedirs(public_dir, exist_ok=True)

    chart_tabs = [
        ("SOFR Strip",   fig_sofr),
        ("FF Strip",     fig_ff),
        ("Meeting Path", fig_path),
        ("CB Lvl Rails", fig_cblvl),
    ]

    # Serialise each figure to a div (plotly.js loaded once in <head>)
    from plotly.io import to_html
    chart_divs = [
        to_html(fig, include_plotlyjs=False, full_html=False, div_id=f"chart{i}")
        for i, (_, fig) in enumerate(chart_tabs)
    ]

    all_tab_labels  = [label for label, _ in chart_tabs] + ["How to Read This"]
    all_tab_content = chart_divs + [LEARN_HTML]

    tab_buttons = "\n".join(
        f'    <button class="tab-btn{" active" if i == 0 else ""}" '
        f'onclick="switchTab({i})">{label}</button>'
        for i, label in enumerate(all_tab_labels)
    )
    tab_panes = "\n".join(
        f'  <div class="tab-pane{" active" if i == 0 else ""}" id="pane{i}">{content}</div>'
        for i, content in enumerate(all_tab_content)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>STIR Dashboard -- Capital Flows Research</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    background: {CFR["bg"]};
    color: {CFR["text"]};
    font-family: "Segoe UI", sans-serif;
  }}

  /* ── Header ── */
  header {{
    padding: 14px 24px 10px;
    border-bottom: 1px solid {CFR["rule"]};
  }}
  header h1 {{
    margin: 0;
    font-size: 15px;
    font-family: Bahnschrift, "Segoe UI", sans-serif;
    color: {CFR["orange"]};
    letter-spacing: .08em;
    text-transform: uppercase;
  }}
  header p {{
    margin: 2px 0 0;
    font-size: 11px;
    color: #666;
    font-family: "IBM Plex Mono", monospace;
  }}

  /* ── Tabs ── */
  .tab-bar {{
    display: flex;
    gap: 2px;
    padding: 10px 24px 0;
    border-bottom: 1px solid {CFR["rule"]};
    flex-wrap: wrap;
  }}
  .tab-btn {{
    background: {CFR["panel"]};
    color: #777;
    border: 1px solid {CFR["rule"]};
    border-bottom: none;
    padding: 7px 20px;
    font-size: 12px;
    font-family: "IBM Plex Mono", monospace;
    letter-spacing: .04em;
    cursor: pointer;
    border-radius: 4px 4px 0 0;
    transition: color .15s, background .15s;
  }}
  .tab-btn:hover   {{ color: {CFR["orangeHot"]}; background: #111; }}
  .tab-btn.active  {{ color: {CFR["orange"]}; background: #0d0d0d;
                      border-color: {CFR["orange"]}; border-bottom-color: #0d0d0d; }}
  .tab-pane        {{ display: none; padding: 20px 24px; }}
  .tab-pane.active {{ display: block; }}

  /* ── Learning guide ── */
  .learn {{ max-width: 860px; }}
  .learn h2 {{
    font-family: Bahnschrift, "Segoe UI", sans-serif;
    color: {CFR["orange"]};
    font-size: 20px;
    margin: 0 0 4px;
    text-transform: uppercase;
    letter-spacing: .06em;
  }}
  .learn .subtitle {{
    color: #666;
    font-size: 12px;
    font-family: "IBM Plex Mono", monospace;
    margin: 0 0 28px;
  }}
  .learn .section {{
    border-left: 2px solid {CFR["rule"]};
    padding-left: 18px;
    margin-bottom: 30px;
  }}
  .learn h3 {{
    color: {CFR["orangeHot"]};
    font-size: 13px;
    font-family: "IBM Plex Mono", monospace;
    letter-spacing: .05em;
    text-transform: uppercase;
    margin: 0 0 10px;
  }}
  .learn p, .learn li {{
    font-size: 13.5px;
    line-height: 1.7;
    color: {CFR["text"]};
    margin: 0 0 10px;
  }}
  .learn ul {{ padding-left: 20px; margin: 0 0 10px; }}
  .learn pre {{
    background: #0d0d0d;
    border: 1px solid {CFR["rule"]};
    border-radius: 4px;
    padding: 12px 16px;
    font-family: "IBM Plex Mono", monospace;
    font-size: 12px;
    color: {CFR["orangeDim"]};
    white-space: pre-wrap;
    margin: 0 0 10px;
  }}
  .learn code {{
    font-family: "IBM Plex Mono", monospace;
    font-size: 12px;
    background: #0d0d0d;
    color: {CFR["orangeHot"]};
    padding: 1px 5px;
    border-radius: 3px;
  }}
  .learn table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
    margin-bottom: 10px;
  }}
  .learn th {{
    text-align: left;
    color: {CFR["orange"]};
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px;
    letter-spacing: .05em;
    text-transform: uppercase;
    border-bottom: 1px solid {CFR["rule"]};
    padding: 6px 10px;
  }}
  .learn td {{
    padding: 7px 10px;
    border-bottom: 1px solid #111;
    color: {CFR["text"]};
    vertical-align: top;
  }}
  .learn tr:hover td {{ background: #0a0a0a; }}
</style>
</head>
<body>
<header>
  <h1>STIR Dashboard &mdash; Capital Flows Research</h1>
  <p>EFFR {OCR:.4f}% &nbsp;|&nbsp; SOFR {SOFR_now:.4f}% &nbsp;|&nbsp; basis {basis_bp:+.1f} bp &nbsp;|&nbsp; {TODAY.isoformat()}</p>
</header>
<div class="tab-bar">
{tab_buttons}
</div>
{tab_panes}
<script>
function switchTab(n) {{
  document.querySelectorAll('.tab-btn').forEach((b,i)  => b.classList.toggle('active', i===n));
  document.querySelectorAll('.tab-pane').forEach((p,i) => p.classList.toggle('active', i===n));
}}
</script>
</body>
</html>"""

    out_path = os.path.join(public_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {out_path}")

    print("\nDone.")
