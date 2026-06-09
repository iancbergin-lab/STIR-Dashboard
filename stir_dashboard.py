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


def load_breakevens(lookback_days: int = 90) -> pd.DataFrame:
    """
    Load TIPS breakeven inflation rates from FRED (no API key required).
    T5YIE  : 5-Year Breakeven Inflation Rate
    T5YIFR : 5-Year, 5-Year Forward Inflation Expectation Rate
    Falls back to flat estimates if FRED is unreachable.
    """
    start_dt = date.today() - timedelta(days=lookback_days + 30)

    def _fetch_fred(series_id: str) -> pd.Series | None:
        try:
            r = requests.get(
                f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
                timeout=10,
            )
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df.index   = pd.to_datetime(df["date"])
            s = df["value"].dropna()
            return s[s.index >= pd.Timestamp(start_dt)].tail(lookback_days)
        except Exception:
            return None

    be5y   = _fetch_fred("T5YIE")
    be5y5y = _fetch_fred("T5YIFR")

    if be5y is None:
        print("  (FRED unreachable -- using flat breakeven fallback: 5Y=2.30%, 5Y5Y=2.50%)")
        idx    = pd.bdate_range(end=date.today(), periods=lookback_days)
        be5y   = pd.Series(2.30, index=idx)
        be5y5y = pd.Series(2.50, index=idx)

    idx = be5y.index.union(be5y5y.index if be5y5y is not None else be5y.index)
    return pd.DataFrame({
        "be5y":   be5y.reindex(idx),
        "be5y5y": (be5y5y.reindex(idx) if be5y5y is not None
                   else pd.Series(dtype=float)),
    }).ffill()


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
        autosize=True,
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
        autosize=True,
    )
    return fig


def plot_meeting_path(
    path: pd.DataFrame,
    effr: float,
    be_now: float | None = None,
) -> go.Figure:
    fig = _base_meeting_fig(path, effr)
    if be_now is not None and not path.empty:
        fig.add_trace(go.Scatter(
            x=path["meeting"],
            y=path["post_rate"] - be_now,
            name=f"Implied real (nominal − {be_now:.2f}% BE)",
            mode="lines+markers",
            line=dict(color=CFR["green"], width=1.8, dash="dot", shape="hv"),
            marker=dict(color=CFR["bg"],
                        line=dict(color=CFR["green"], width=1.5),
                        size=7),
            hovertemplate="%{x|%Y-%m-%d}<br>Implied real: %{y:.2f}%<extra></extra>",
        ))
        fig.add_hline(
            y=0, line_dash="dash", line_color="#333", line_width=0.8,
            annotation_text="0% real", annotation_position="right",
            annotation_font=dict(color="#555", size=9),
        )
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


def plot_real_rates(
    ref_rates:  pd.DataFrame,
    breakevens: pd.DataFrame,
    path:       pd.DataFrame,
    ocr:        float,
) -> go.Figure:
    """
    Tab: Real Rates
    Historical: nominal EFFR (orange), 5Y TIPS breakeven (green),
                real rate = EFFR − breakeven (red).
    Forward:    ZQ-implied nominal path (dashed orange),
                implied real path = nominal − current breakeven (dashed green).
    """
    # Align historical series — normalise both indices to date-only before joining
    # so that NY Fed (business-day) and FRED (calendar-day) timestamps match.
    effr_s = ref_rates["effr"].copy()
    effr_s.index = pd.to_datetime(effr_s.index).normalize()
    be5y_s = breakevens["be5y"].copy()
    be5y_s.index = pd.to_datetime(be5y_s.index).normalize()

    hist = pd.DataFrame({
        "nominal": effr_s,
        "be5y":    be5y_s,
    }).sort_index()
    # Forward-fill across any 1-2 day gaps (e.g. FRED lags by one business day)
    hist = hist.ffill().dropna()
    # Sanity-clip: reject any row where either value looks unreasonable
    hist = hist[(hist["nominal"] > 0) & (hist["nominal"] < 30)
                & (hist["be5y"] > -2) & (hist["be5y"] < 15)]
    hist["real"] = hist["nominal"] - hist["be5y"]

    be_now      = float(breakevens["be5y"].iloc[-1])
    real_now    = ocr - be_now

    fig = go.Figure()

    # ── Historical lines ──────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["nominal"],
        name="Nominal EFFR",
        line=dict(color=CFR["orangeHot"], width=2.2),
        hovertemplate="%{x|%b %d %Y}<br>Nominal: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["be5y"],
        name="5Y TIPS Breakeven",
        line=dict(color=CFR["green"], width=1.8),
        hovertemplate="%{x|%b %d %Y}<br>5Y breakeven: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["real"],
        name="Real Rate (EFFR − 5Y BE)",
        line=dict(color=CFR["red"], width=1.8),
        hovertemplate="%{x|%b %d %Y}<br>Real: %{y:.2f}%<extra></extra>",
    ))

    # ── Forward implied paths (from ZQ meeting path) ──────────────────────────
    if not path.empty:
        fig.add_trace(go.Scatter(
            x=path["meeting"], y=path["post_rate"],
            name="Implied Nominal (ZQ path)",
            mode="lines+markers",
            line=dict(color=CFR["orangeHot"], width=1.6, dash="dot", shape="hv"),
            marker=dict(size=6, color=CFR["bg"],
                        line=dict(color=CFR["orangeHot"], width=1.5)),
            hovertemplate="%{x|%b %d %Y}<br>Fwd nominal: %{y:.2f}%<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=path["meeting"], y=path["post_rate"] - be_now,
            name=f"Implied Real (nominal − {be_now:.2f}% BE)",
            mode="lines+markers",
            line=dict(color=CFR["green"], width=1.6, dash="dot", shape="hv"),
            marker=dict(size=6, color=CFR["bg"],
                        line=dict(color=CFR["green"], width=1.5)),
            hovertemplate="%{x|%b %d %Y}<br>Fwd real: %{y:.2f}%<extra></extra>",
        ))

    # ── Reference lines ───────────────────────────────────────────────────────
    fig.add_hline(
        y=0, line_dash="dash", line_color="#333", line_width=0.8,
        annotation_text="0% real", annotation_position="right",
        annotation_font=dict(color="#555", size=9),
    )
    fig.add_hline(
        y=real_now,
        line_dash="dot", line_color=CFR["red"], line_width=0.8,
        annotation_text=f"current real {real_now:+.2f}%",
        annotation_position="right",
        annotation_font=dict(color=CFR["red"], size=9),
    )

    fig.update_layout(
        title=dict(
            text="REAL RATES -- NOMINAL · BREAKEVEN · REAL (HISTORICAL + IMPLIED)",
            font=dict(color=CFR["orange"], family="Bahnschrift", size=18),
        ),
        template="plotly_dark",
        paper_bgcolor=CFR["bg"],
        plot_bgcolor="#050505",
        font=dict(family="Segoe UI", color=CFR["text"]),
        yaxis_title="Rate (%)",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, font=dict(size=11),
        ),
        margin=dict(l=60, r=60, t=100, b=40),
        height=520,
        autosize=True,
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

    # 2b. TIPS breakevens from FRED
    print("Fetching TIPS breakevens from FRED ...")
    breakevens = load_breakevens()
    BE_NOW     = float(breakevens["be5y"].iloc[-1])
    REAL_NOW   = OCR - BE_NOW
    print(f"5Y TIPS breakeven: {BE_NOW:.4f}%")
    print(f"Real rate (EFFR-BE): {REAL_NOW:+.4f}%")

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
    fig_path  = plot_meeting_path(path, OCR, be_now=BE_NOW)
    fig_cblvl = plot_cb_lvl(path, OCR)
    fig_real  = plot_real_rates(ref_rates, breakevens, path, OCR)

    # 10. Per-chart inline explainers (appended below each chart in its tab)
    _EXPL_SOFR = """
<div class="explainer">
  <h4>How to Read This Chart</h4>
  <div class="expl-section">
    <h5>What is SOFR?</h5>
    <p><strong>SOFR</strong> stands for <strong>Secured Overnight Financing Rate</strong>.
    Published daily by the New York Fed, it is the benchmark short-term interest rate for
    the US dollar — the rate at which the largest institutions lend to each other overnight,
    using US Treasury securities as collateral. Because these are real, observable transactions
    (not estimates), SOFR is considered highly reliable. It replaced LIBOR in 2023 and is now
    the foundation of trillions of dollars of financial contracts worldwide.</p>
  </div>
  <div class="expl-section">
    <h5>What Are SR3 Futures?</h5>
    <p><strong>SR3</strong> (CME 3-Month SOFR futures) are exchange-traded contracts that let
    market participants bet on — or hedge against — where 3-month compounded SOFR will be in
    the future. Each contract expires on an IMM date (the third Wednesday of March, June,
    September, or December) and settles against the compounded daily SOFR over the 3-month
    period ending on that date. With 8–12 quarterly expirations active at any time, the SR3
    strip gives a clear market-implied view of short-term rates up to 2–3 years ahead.</p>
  </div>
  <div class="expl-section">
    <h5>The 100-Minus-Rate Convention</h5>
    <p>All STIR futures use the <strong>IMM price convention</strong>: price = 100 &minus;
    implied rate. A price of <code>96.38</code> means the market expects the rate to be
    <code>3.62%</code>. This inverse relationship means prices <em>rise</em> when cuts are
    expected (toward 100) and <em>fall</em> when hikes are expected (away from 100). It can
    feel counterintuitive at first — just remember: higher price = lower rate expectation.</p>
  </div>
  <div class="expl-section">
    <h5>Reading the Bar Chart</h5>
    <p>Each bar is one quarterly contract, arranged left to right from nearest to furthest
    expiry. The bar height is the implied 3-month SOFR rate for that period. The
    <strong>dashed orange line</strong> is today's Effective Federal Funds Rate (EFFR) —
    the current policy anchor.</p>
    <ul>
      <li><strong>Bars below the line</strong> &rarr; cuts are priced in for that period</li>
      <li><strong>Bars above the line</strong> &rarr; hikes are priced in</li>
      <li><strong>Flat bars at the line</strong> &rarr; no change expected; policy on hold</li>
    </ul>
    <p>The <strong>bright orange bar</strong> marks the <em>terminal contract</em> — the
    expiry where rates are expected to reach their extreme (highest in a hiking cycle, lowest
    in a cutting cycle) before reversing. Every bar beyond it reflects the market's view of
    the normalisation path back toward a neutral rate. A steep slope toward the terminal means
    swift, decisive action is priced; a gentle slope means a slow, gradual path.</p>
  </div>
  <div class="expl-section">
    <h5>SR3 vs ZQ — What Each Is Good For</h5>
    <p>SR3 is quarterly; ZQ (the FF Strip tab) is monthly. Use SR3 for the big-picture
    direction — where rates are heading over the next 1–3 years. Use ZQ for precision on
    individual FOMC meeting dates. Together they cross-check each other: if the SR3 strip
    is pricing deep cuts but the near-term ZQ contracts are flat, the market is saying
    "eventually, but not yet."</p>
  </div>
</div>"""

    _EXPL_FF = """
<div class="explainer">
  <h4>How to Read This Chart</h4>
  <div class="expl-section">
    <h5>What is the Fed Funds Rate?</h5>
    <p>The <strong>Federal Funds Rate (FFR)</strong> is the interest rate at which US banks
    lend their reserve balances to each other on an overnight basis. It is the primary lever
    of US monetary policy — when the Fed "raises rates," it is adjusting its target range for
    this rate. The <strong>Effective Federal Funds Rate (EFFR)</strong> is the volume-weighted
    median of all actual overnight transactions, published each morning by the NY Fed. It
    almost always sits within the Fed's target band (currently 25bp wide), making it a
    reliable read of where policy actually stands day-to-day.</p>
  </div>
  <div class="expl-section">
    <h5>What Are ZQ Futures?</h5>
    <p><strong>ZQ</strong> (CME 30-Day Federal Funds futures) settle against the
    <em>arithmetic average of the daily EFFR</em> across the entire calendar month of expiry.
    With monthly contracts extending 18+ months forward, ZQ gives finer granularity than the
    quarterly SR3 strip — crucially, each ZQ contract maps to exactly one calendar month, and
    most FOMC meetings fall in a unique month. This makes ZQ the standard tool for extracting
    exact per-meeting rate expectations.</p>
  </div>
  <div class="expl-section">
    <h5>Why Monthly Settlement Matters for Meeting Analysis</h5>
    <p>Suppose a Fed meeting falls on day 18 of a 30-day month. The ZQ contract for that
    month settles against the full-month average, which is a weighted blend of 17 days at
    the <em>pre-meeting</em> rate and 13 days at the <em>post-meeting</em> rate. Because
    we know the number of days on each side, we can solve algebraically for the post-meeting
    rate the market is pricing. That calculation is the foundation of the Meeting Path tab.</p>
    <p>A practical consequence: if you see two adjacent ZQ bars that are nearly the same
    height, it does <em>not</em> mean both months are priced identically. It may mean a
    meeting-day effect in the second month is being masked by the pre-meeting days pulling
    the average back toward the previous level.</p>
  </div>
  <div class="expl-section">
    <h5>Reading the Chart</h5>
    <p>Bars ordered left-to-right by expiry month. The <strong>dashed orange line</strong>
    is today's EFFR. The <strong>bright orange bar</strong> is the terminal month — where
    the monthly average rate peaks or troughs.</p>
    <ul>
      <li><strong>Sharp jump between adjacent monthly bars</strong> &rarr; a rate move is
      priced specifically in the month where the jump occurs</li>
      <li><strong>Gradual slope over several months</strong> &rarr; the market is hedging
      between a move now or slightly later — no single meeting is fully priced</li>
      <li><strong>Flat for several months then a drop</strong> &rarr; the market expects
      the Fed to hold until a specific trigger, then cut swiftly</li>
    </ul>
  </div>
  <div class="expl-section">
    <h5>Comparing the ZQ and SR3 Strips</h5>
    <p>The terminal rate implied by ZQ's near-term contracts should roughly match what SR3
    prices for the same period. Persistent divergences between the two can signal: (1) a
    SOFR-EFFR basis trade (the rates themselves are expected to diverge), or (2) a
    technical supply/demand imbalance in one market. For policy direction purposes, treating
    them as cross-checks is standard practice.</p>
  </div>
</div>"""

    _EXPL_PATH = """
<div class="explainer">
  <h4>How to Read This Chart</h4>
  <div class="expl-section">
    <h5>What This Chart Shows</h5>
    <p>The Meeting Path extracts the <strong>exact overnight rate the market implies
    immediately after each FOMC meeting</strong>. This is different from the ZQ bar chart,
    which shows monthly averages. By doing the weighted-day arithmetic, we isolate the
    pure post-decision rate — removing the "noise" of pre-meeting days in the monthly
    average and giving you a clean step-function view of the expected policy path.</p>
  </div>
  <div class="expl-section">
    <h5>The Formula</h5>
    <p>If a meeting falls on day <em>D</em> of a month with <em>N</em> days total, and the
    ZQ contract for that month implies an average rate of <em>R</em>, then:</p>
    <p><code>post_rate = (R &times; N &minus; (D&minus;1) &times; prev_rate) &divide; (N&minus;D+1)</code></p>
    <p>Where <em>prev_rate</em> is the rate that prevailed before the meeting (taken from
    the prior meeting's result, or today's EFFR for the first meeting). This isolates
    exactly what rate must hold for the remaining days of the month to produce the observed
    futures price.</p>
  </div>
  <div class="expl-section">
    <h5>Reading the Step Function</h5>
    <p>Each <strong>dot</strong> is one future FOMC meeting. The horizontal segments between
    dots represent periods where no change is expected. Vertical steps at dots represent
    priced-in moves:</p>
    <ul>
      <li><strong>Step downward</strong> at a meeting &rarr; a rate <em>cut</em> is priced
      for that date</li>
      <li><strong>Step upward</strong> &rarr; a rate <em>hike</em> is priced</li>
      <li><strong>No step (dot at same level as previous)</strong> &rarr; the market
      expects the Fed to hold at that meeting</li>
      <li><strong>Dot between two 25bp levels</strong> &rarr; the market is split between
      two outcomes (e.g. 60% chance of a cut, 40% hold)</li>
    </ul>
    <p>The gap between the <em>first dot</em> and the <strong>dashed EFFR line</strong> tells
    you how much move is priced for the very next meeting. The gap between the <em>last dot</em>
    and the EFFR line is the total cumulative easing or tightening priced across the entire
    visible horizon.</p>
  </div>
  <div class="expl-section">
    <h5>The Green Dotted Line &mdash; Implied Real Rate</h5>
    <p>The <strong>green dotted line</strong> shows the <em>real rate</em> implied at each
    meeting — calculated as the nominal post-meeting rate minus the current 5-year TIPS
    breakeven inflation expectation. Real rates are what actually drive economic behaviour:
    a 4% nominal rate with 3% inflation is only mildly restrictive in real terms; the same
    4% rate with 1% inflation is very tight.</p>
    <ul>
      <li><strong>Green line above the grey 0% boundary</strong> &rarr; policy remains
      restrictive in real terms after that meeting — the economy is still being squeezed</li>
      <li><strong>Green line below 0%</strong> &rarr; the market prices genuinely
      accommodative real rates — rare outside recessions or crisis responses</li>
      <li><strong>Green and orange lines converging</strong> &rarr; breakevens are near
      zero; the distinction between nominal and real policy barely matters (deflation risk)</li>
    </ul>
  </div>
</div>"""

    _EXPL_CBLVL = """
<div class="explainer">
  <h4>How to Read This Chart</h4>
  <div class="expl-section">
    <h5>Why the Rails Exist</h5>
    <p>Central banks do not set rates to arbitrary decimal places. The Federal Reserve always
    moves in <strong>25 basis point (0.25%) increments</strong> — so the possible outcomes
    after any meeting are: unchanged, &plusmn;25bp, &plusmn;50bp (two moves in one meeting,
    rare but it happens), etc. The horizontal dashed lines — the "rails" — mark every
    reachable 25bp level within &plusmn;150bp of the current settled rate. The
    <strong>solid bright orange line</strong> is today's policy rate rounded to the nearest
    25bp: the rail the Fed is currently sitting on.</p>
  </div>
  <div class="expl-section">
    <h5>How to Read Probability From Position</h5>
    <p>Because rates can only land on rails, a meeting path dot that plots <em>between</em>
    two rails represents a probability-weighted blend. The closer the dot is to a rail, the
    higher the implied probability of that outcome:</p>
    <ul>
      <li><strong>Dot exactly on a rail</strong> &rarr; ~100% probability of landing there</li>
      <li><strong>Dot exactly halfway between two rails</strong> &rarr; 50/50 split</li>
      <li><strong>Dot 75% of the way to the next rail down</strong> &rarr; ~75% cut /
      ~25% hold</li>
    </ul>
    <p>More precisely: <code>P(move to lower rail) = (current rate &minus; dot) &divide; 0.25</code>,
    and <code>P(hold) = 1 &minus; P(move)</code>. This is exactly how CME FedWatch
    probabilities are calculated from futures prices.</p>
  </div>
  <div class="expl-section">
    <h5>Counting Moves Across the Cycle</h5>
    <p>Follow the step-function from left to right. Each time the path drops through a rail,
    one 25bp cut has been fully priced. Count the number of rails crossed to get the
    <strong>total number of cuts (or hikes)</strong> priced to a given horizon. If the path
    ends 3 rails below the starting orange line by year-end, the market is pricing
    approximately 3&times;25bp = 75bp of cuts over that period.</p>
    <p>Dots that hover stubbornly between the top two rails for multiple consecutive meetings
    indicate the market is genuinely uncertain about the near-term path — high event risk,
    data dependency, or mixed signals from the Fed.</p>
  </div>
  <div class="expl-section">
    <h5>The Soft Landing Signal</h5>
    <p>In a "soft landing" scenario — where the Fed cuts rates but avoids recession — the
    cutting cycle typically ends with the path settling on a rail that still keeps the real
    rate positive (above 0%). If the path stops two or three rails above the level that
    would push real rates negative, the market believes the Fed will achieve a controlled
    normalisation without being forced into emergency accommodation. If the path plunges
    through that level, the market is hedging a harder landing.</p>
  </div>
</div>"""

    _EXPL_REAL = """
<div class="explainer">
  <h4>How to Read This Chart</h4>
  <div class="expl-section">
    <h5>Nominal vs Real Rates &mdash; Why It Matters</h5>
    <p>The <strong>nominal rate</strong> is the number the Fed announces — currently 3.62%.
    But that number in isolation tells you very little about how tight or loose monetary policy
    actually is. A 3.62% rate when inflation is running at 3.5% is nearly neutral — money is
    barely more expensive than inflation, so borrowing costs are almost free in real terms.
    The same 3.62% rate with 1% inflation is genuinely restrictive — businesses and households
    are paying 2.6% above inflation to borrow. The <strong>real rate</strong> (nominal minus
    inflation expectation) is what the economy actually feels.</p>
  </div>
  <div class="expl-section">
    <h5>What TIPS Breakevens Measure</h5>
    <p>A <strong>TIPS</strong> (Treasury Inflation-Protected Security) is a US government bond
    whose principal automatically adjusts with CPI. The <strong>breakeven inflation rate</strong>
    is calculated as: <code>nominal Treasury yield &minus; TIPS yield</code> for the same
    maturity. If a 5-year nominal Treasury yields 4.5% and the 5-year TIPS yields 2.2%, the
    5Y breakeven is 2.3%. This means the bond market is indifferent between the two bonds only
    if average CPI inflation over 5 years turns out to be exactly 2.3%. If you expect more
    inflation, TIPS wins; less, the nominal bond wins. The breakeven is therefore a continuous,
    real-money market vote on where inflation is headed — updated every trading day.</p>
  </div>
  <div class="expl-section">
    <h5>The Five Lines</h5>
    <table>
      <thead><tr><th>Line</th><th>What It Is</th></tr></thead>
      <tbody>
        <tr><td><span style="color:#FF9533">&#9644; Orange solid</span></td><td><strong>Nominal EFFR</strong> — the actual daily policy rate over the past 90 days</td></tr>
        <tr><td><span style="color:#00E676">&#9644; Green solid</span></td><td><strong>5Y TIPS Breakeven</strong> — market-implied inflation expectation, daily</td></tr>
        <tr><td><span style="color:#FF1744">&#9644; Red solid</span></td><td><strong>Real Rate</strong> = EFFR &minus; breakeven. The inflation-adjusted cost of money</td></tr>
        <tr><td><span style="color:#FF9533">&#9148; Orange dotted</span></td><td><strong>Implied nominal path</strong> — where ZQ futures say the policy rate will be at each future FOMC meeting</td></tr>
        <tr><td><span style="color:#00E676">&#9148; Green dotted</span></td><td><strong>Implied real path</strong> = implied nominal &minus; today's breakeven. A &ldquo;what if inflation stays here&rdquo; projection</td></tr>
      </tbody>
    </table>
  </div>
  <div class="expl-section">
    <h5>Key Signals to Watch</h5>
    <ul>
      <li><strong>Real rate climbing while the nominal rate is flat</strong> &rarr; inflation
      expectations are falling. The Fed is getting tighter without doing anything &mdash;
      sometimes called &ldquo;passive tightening.&rdquo; This happened in 2023 as inflation
      fell faster than the Fed cut rates.</li>
      <li><strong>Real rate turning sharply negative</strong> &rarr; policy is deeply
      accommodative. Historically associated with the zero-lower-bound era (2009&ndash;2015,
      2020&ndash;2022). Negative real rates are powerful stimulus — cheap money floods into
      risk assets, housing, and investment.</li>
      <li><strong>Breakeven rising faster than EFFR</strong> &rarr; the market thinks the
      Fed is falling behind on inflation. If left unaddressed, the Fed may need to hike more
      aggressively later to regain credibility.</li>
      <li><strong>Implied real path staying comfortably positive at all future meetings</strong>
      &rarr; the market believes the Fed's cutting cycle will be controlled and modest —
      a textbook soft landing. Policy eases but never becomes stimulative.</li>
      <li><strong>Implied real path crossing below zero in the forward section</strong> &rarr;
      the market is hedging a scenario where the Fed is forced into emergency accommodation —
      recession or financial-system stress.</li>
    </ul>
  </div>
  <div class="expl-section">
    <h5>Caveat on the Forward Real Path</h5>
    <p>The green dotted line holds today's breakeven <em>flat</em> into the future. In reality,
    if the Fed cuts aggressively, inflation expectations may rise (more stimulus = more
    inflation risk), which would push the real rate even lower than the dotted line shows.
    Conversely, if rate cuts signal confidence in tamed inflation, breakevens may fall, making
    the real rate less negative. Treat the dotted line as a baseline scenario, not a forecast.</p>
  </div>
</div>"""

    EXPLAINERS = [_EXPL_SOFR, _EXPL_FF, _EXPL_PATH, _EXPL_CBLVL, _EXPL_REAL]

    # 10b. (standalone "How to Read This" tab removed -- explainers now inline below each chart)

    # 11. Save combined single-file dashboard with tabs -- NOTE: removed old LEARN_HTML block below

    # 11. [placeholder to preserve numbering]
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
        ("Real Rates",   fig_real),
    ]

    # Serialise each figure to a div (plotly.js loaded once in <head>)
    from plotly.io import to_html
    chart_divs = [
        to_html(fig, include_plotlyjs=False, full_html=False, div_id=f"chart{i}",
                config={"responsive": True})
        for i, (_, fig) in enumerate(chart_tabs)
    ]

    all_tab_labels  = [label for label, _ in chart_tabs]
    all_tab_content = [div + expl for div, expl in zip(chart_divs, EXPLAINERS)]

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
  .tab-pane .js-plotly-plot,
  .tab-pane .plotly-graph-div {{ width: 100% !important; }}

  /* ── Per-chart inline explainers ── */
  .explainer {{
    max-width: 900px;
    margin: 36px 0 0;
    padding: 24px 28px;
    border-top: 1px solid {CFR["rule"]};
    background: #040404;
  }}
  .explainer > h4 {{
    color: {CFR["orange"]};
    font-family: "IBM Plex Mono", monospace;
    font-size: 10px;
    letter-spacing: .12em;
    text-transform: uppercase;
    margin: 0 0 22px;
    padding-bottom: 10px;
    border-bottom: 1px solid {CFR["rule"]};
  }}
  .expl-section {{ margin-bottom: 26px; }}
  .expl-section h5 {{
    color: {CFR["orangeHot"]};
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .06em;
    border-left: 2px solid {CFR["rule"]};
    padding-left: 10px;
    margin: 0 0 10px;
  }}
  .explainer p, .explainer li {{
    font-size: 13.5px;
    line-height: 1.75;
    color: {CFR["text"]};
    margin: 0 0 10px;
  }}
  .explainer ul {{ padding-left: 20px; margin: 0 0 10px; }}
  .explainer code {{
    font-family: "IBM Plex Mono", monospace;
    font-size: 12px;
    background: #0d0d0d;
    color: {CFR["orangeHot"]};
    padding: 2px 6px;
    border-radius: 3px;
  }}
  .explainer table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin: 8px 0 14px;
  }}
  .explainer th {{
    text-align: left;
    color: {CFR["orange"]};
    font-family: "IBM Plex Mono", monospace;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: .06em;
    padding: 6px 10px;
    border-bottom: 1px solid {CFR["rule"]};
  }}
  .explainer td {{
    padding: 7px 10px;
    border-bottom: 1px solid #111;
    color: {CFR["text"]};
    vertical-align: top;
    font-size: 13px;
  }}
  .explainer tr:hover td {{ background: #090909; }}
</style>
</head>
<body>
<header>
  <h1>STIR Dashboard &mdash; Capital Flows Research</h1>
  <p>EFFR {OCR:.4f}% &nbsp;|&nbsp; SOFR {SOFR_now:.4f}% &nbsp;|&nbsp; basis {basis_bp:+.1f} bp &nbsp;|&nbsp; 5Y BE {BE_NOW:.2f}% &nbsp;|&nbsp; real {REAL_NOW:+.2f}% &nbsp;|&nbsp; {TODAY.isoformat()}</p>
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
