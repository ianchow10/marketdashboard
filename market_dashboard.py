#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股市寬與動能監察（免費數據版）

免費數據來源：
  股票清單與行業：Nasdaq 股票篩選器（api.nasdaq.com）
  日線價格與期權：Yahoo Finance（經 yfinance）

用法：
  python market_dashboard.py          正式執行，輸出 docs/index.html
  python market_dashboard.py --demo   用隨機假數據測試版面，輸出 docs/demo.html
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ============================== 設定（可自行修改） ==============================
MIN_PRICE = 5.0               # 市寬樣本：收市價下限
MIN_VOLUME = 300_000          # 市寬樣本：當日成交量下限
BIG_MOVE = 0.04               # 4% 動量
LEADER_MIN_DOLLAR_VOL = 5_000_000   # 動力股：20 日平均成交額下限
LEADER_MIN_RET = 0.20         # 動力股：季度升幅下限
LEADER_LOOKBACK = 63          # 一季約 63 個交易日
TABLE_DAYS = 20               # 每日監察表顯示幾多日
PRE_MIN_PRICE = 3.0           # 下載前粗篩（減少下載量）
PRE_MIN_VOLUME = 100_000
WATCHLIST = ["SNDK", "AMD", "ARM", "MU", "MRVL", "KLAC", "AVGO", "AMAT", "LRCX"]
TARGET_DTE = 30               # 期權：揀最接近 30 日到期的合約計 IV
OI_WINDOW_DAYS = 45           # 期權：計 OI 牆時包括幾多日內到期的合約

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"

# 行業歸類：按 Nasdaq 的 industry 字串關鍵字，順序決定優先次序
INDUSTRY_RULES = [
    ("Biotech & Pharma", ["biotech", "pharmaceutical", "biological product", "in vitro"]),
    ("Semiconductors", ["semiconductor"]),
    ("Software & IT Services", ["software", "edp services", "computer services",
                                "data processing", "internet", "information services"]),
    ("Medical Devices & Instruments", ["medical/dental", "medical electronics",
                                       "medical specialities", "ophthalmic", "electromedical",
                                       "surgical", "instruments"]),
    ("Health Care Services", ["hospital", "nursing", "health", "managed care", "medical laboratories"]),
    ("Banks", ["bank", "savings institution"]),
    ("Insurance", ["insur"]),
    ("Financial Services", ["investment bankers", "investment managers", "finance",
                            "financial", "credit", "brokers"]),
    ("Real Estate & REITs", ["real estate"]),
    ("Metals & Coal Mining", ["mining", "precious metals", "coal"]),
    ("Oil & Gas", ["oil", "natural gas", "petroleum"]),
    ("Utilities", ["electric utilities", "water supply", "power generation", "utilit"]),
    ("Telecom & Media", ["telecommunications", "broadcasting", "cable", "newspapers",
                         "publishing", "movies", "advertising"]),
    ("Computer & Electronic Hardware", ["computer communications", "computer peripheral",
                                        "computer manufacturing", "electronic components",
                                        "electrical products", "radio and television"]),
    ("Aerospace & Defense", ["aerospace", "military", "ordnance"]),
    ("Industrial Machinery", ["industrial machinery", "machinery", "engineering",
                              "construction", "electrical"]),
    ("Transportation & Logistics", ["trucking", "air freight", "railroad", "marine",
                                    "transportation", "airline", "shipping"]),
    ("Autos", ["auto"]),
    ("Retail & Restaurants", ["retail", "stores", "restaurants", "catalog"]),
    ("Consumer Products", ["food", "beverage", "apparel", "consumer", "household",
                           "recreational", "hotels", "tobacco", "cosmetic", "leisure",
                           "home furnishings", "shoe"]),
    ("Chemicals & Materials", ["chemical", "plastics", "paper", "containers", "packaging",
                               "steel", "building materials", "metal"]),
    ("Business Services", ["business services", "professional services",
                           "commercial services", "consulting", "education"]),
]


def classify(industry: str, sector: str) -> str:
    s = (industry or "").lower()
    for name, keys in INDUSTRY_RULES:
        if any(k in s for k in keys):
            return name
    return sector.strip() if sector and sector.strip() else "Unclassified"


# ================================= 取得數據 =================================
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
BAD_NAME = r"(?i)warrant|\bunits?\b|\brights?\b|preferred|debenture|\bnotes?\b|%|1/\d"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")


def fetch_universe() -> pd.DataFrame:
    """回傳欄位：yahoo, name, group。只包括普通股及 ADR（不含 ETF、優先股、權證等）。"""
    import requests
    try:
        r = requests.get("https://api.nasdaq.com/api/screener/stocks",
                         params={"tableonly": "true", "limit": "25000", "download": "true"},
                         headers=UA, timeout=60)
        r.raise_for_status()
        df = pd.DataFrame(r.json()["data"]["rows"])
        df["symbol"] = df["symbol"].str.strip()
        df = df[~df["symbol"].str.contains(r"\^", regex=True)]
        df = df[~df["name"].str.contains(BAD_NAME, regex=True, na=False)]
        price, vol = _num(df["lastsale"]), _num(df["volume"])
        df = df[(price >= PRE_MIN_PRICE) & (vol >= PRE_MIN_VOLUME)]
        df["group"] = [classify(i, s) for i, s in zip(df.get("industry", ""), df.get("sector", ""))]
        print(f"[universe] Nasdaq 篩選器：{len(df)} 隻")
    except Exception as e:  # 後備：Nasdaq Trader 代號檔（沒有行業資料）
        print(f"[universe] Nasdaq 篩選器失敗（{e}），改用 nasdaqtraded.txt")
        txt = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt",
                           headers=UA, timeout=60).text
        df = pd.read_csv(pd.io.common.StringIO(txt), sep="|")
        df = df[(df["ETF"] == "N") & (df["Test Issue"] == "N")]
        df = df.rename(columns={"Symbol": "symbol", "Security Name": "name"})
        df = df[~df["symbol"].astype(str).str.contains(r"[\^\$\.]", regex=True)]
        df = df[~df["name"].str.contains(BAD_NAME, regex=True, na=False)]
        df["group"] = "Unclassified"
    df["yahoo"] = df["symbol"].astype(str).str.replace("/", "-", regex=False)
    return df[["yahoo", "name", "group"]].drop_duplicates("yahoo").reset_index(drop=True)


def download_prices(tickers: list[str], period: str = "15mo", chunk: int = 150) -> dict:
    """分批下載日線，回傳 {'Close','High','Low','Volume'} 各一個 DataFrame（index=日期, columns=代號）。"""
    import yfinance as yf
    parts = {k: [] for k in ("Close", "High", "Low", "Volume")}
    total = len(tickers)
    for i in range(0, total, chunk):
        batch = tickers[i:i + chunk]
        data = None
        for attempt in range(4):
            try:
                data = yf.download(batch, period=period, interval="1d", auto_adjust=True,
                                   group_by="column", threads=True, progress=False)
                if data is not None and not data.empty:
                    break
            except Exception as e:
                print(f"  批次 {i} 第 {attempt + 1} 次失敗：{e}")
            time.sleep(5 * (attempt + 1))
        if data is None or data.empty:
            continue
        if not isinstance(data.columns, pd.MultiIndex):
            data.columns = pd.MultiIndex.from_product([data.columns, batch])
        for k in parts:
            if k in data.columns.get_level_values(0):
                parts[k].append(data[k])
        print(f"[prices] {min(i + chunk, total)}/{total}")
        time.sleep(1)
    out = {}
    for k, v in parts.items():
        df = pd.concat(v, axis=1) if v else pd.DataFrame()
        df = df.loc[:, ~df.columns.duplicated()].sort_index()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[k] = df
    keep = out["Close"].dropna(how="all").index
    return {k: v.reindex(keep) for k, v in out.items()}


# ================================= 計算 =================================
def atr_distance(c: pd.Series, h: pd.Series, l: pd.Series) -> pd.Series:
    """（收市 − 50 日 EMA）÷ 14 日 ATR"""
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    ema = c.ewm(span=50, adjust=False).mean()
    return (c - ema) / atr


def compute_metrics(px: dict, bench: dict):
    c, v = px["Close"], px["Volume"]
    ret = c.pct_change(fill_method=None)
    elig = (c >= MIN_PRICE) & (v >= MIN_VOLUME)

    m = pd.DataFrame(index=c.index)
    m["up"] = (elig & (ret >= BIG_MOVE)).sum(axis=1)
    m["dn"] = (elig & (ret <= -BIG_MOVE)).sum(axis=1)
    m["n"] = elig.sum(axis=1)
    for w in (20, 50, 200):
        ma = c.rolling(w, min_periods=w).mean()
        base = elig & ma.notna()
        m[f"p{w}"] = 100 * (base & (c > ma)).sum(axis=1) / base.sum(axis=1).replace(0, np.nan)

    dv20 = (c * v).rolling(20, min_periods=15).mean()
    r63 = c / c.shift(LEADER_LOOKBACK) - 1
    analysable = (c >= MIN_PRICE) & (dv20 >= LEADER_MIN_DOLLAR_VOL) & r63.notna()
    leader = analysable & (r63 >= LEADER_MIN_RET)
    m["leaders"] = leader.sum(axis=1)
    m["analysable"] = analysable.sum(axis=1)

    # MLI：用「上一個交易日」的動力股名單，計今日平均升跌（避免今日大升股自動入選造成偏差）
    prev_leader = leader.shift(1, fill_value=False)
    lr = ret.where(prev_leader)
    m["mli_n"] = lr.notna().sum(axis=1)
    m["mli"] = lr.mean(axis=1) * 100
    m["mli_up"] = 100 * (lr > 0).sum(axis=1) / m["mli_n"].replace(0, np.nan)

    bc, bh, bl = bench["Close"], bench["High"], bench["Low"]
    for sym, col in (("SPY", "spy_atr"), ("QQQ", "qqq_atr")):
        if sym in bc:
            m[col] = atr_distance(bc[sym], bh[sym], bl[sym]).reindex(m.index)
        else:
            m[col] = np.nan
    m["spx"] = bc["^GSPC"].reindex(m.index) if "^GSPC" in bc else np.nan

    m = m[m["n"] > 0].dropna(subset=["p200"])
    return m, leader.reindex(m.index), analysable.reindex(m.index), ret.reindex(m.index), r63.reindex(m.index)


def sector_table(leader_today: pd.Series, analysable_today: pd.Series, groups: pd.Series) -> pd.DataFrame:
    g = groups.reindex(leader_today.index).fillna("Unclassified")
    a = analysable_today.fillna(False).astype(bool)
    L = leader_today.fillna(False).astype(bool) & a
    t = pd.DataFrame({"pool": g[a].value_counts(), "lead": g[L].value_counts()}).fillna(0)
    t["share"] = 100 * t["lead"] / max(t["lead"].sum(), 1)
    t["pool_pct"] = 100 * t["pool"] / max(t["pool"].sum(), 1)
    t["pen"] = 100 * t["lead"] / t["pool"].replace(0, np.nan)
    t["excess"] = t["share"] - t["pool_pct"]
    return t[t["lead"] > 0].sort_values(["lead", "excess"], ascending=False)


# ================================= 期權 =================================
def zone_of(d20: float) -> str:
    if not math.isfinite(d20):
        return "—"
    d = d20 * 100
    if d >= 10:
        return "過熱"
    if d >= 3:
        return "偏強"
    if d > -2:
        return "中性"
    if d > -10:
        return "偏弱"
    return "超賣"


def _atm_iv(calls: pd.DataFrame, puts: pd.DataFrame, spot: float) -> float:
    vals = []
    for df in (calls, puts):
        d = df[(df["impliedVolatility"] > 0.03) & (df["impliedVolatility"] < 5)]
        if d.empty:
            continue
        d = d.assign(dist=(d["strike"] - spot).abs()).nsmallest(2, "dist")
        vals += d["impliedVolatility"].tolist()
    return float(np.median(vals)) * 100 if vals else float("nan")


def option_snapshot(tk: str, c: pd.Series, demo: bool = False) -> dict:
    c = c.dropna()
    row = {"ticker": tk, "spot": float("nan")}
    if len(c) < 30:
        return row
    spot = float(c.iloc[-1])
    asof = c.index[-1].date()
    row.update(spot=spot)
    for w in (20, 50, 200):
        ma = c.rolling(w).mean().iloc[-1]
        row[f"d{w}"] = spot / ma - 1 if math.isfinite(ma) else float("nan")
    row["rv20"] = float(np.log(c).diff().iloc[-20:].std() * math.sqrt(252) * 100)
    row["zone"] = zone_of(row["d20"])

    if demo:
        rng = np.random.default_rng(abs(hash(tk)) % 2**32)
        row.update(iv=float(rng.uniform(30, 75)), put_wall=float(rng.uniform(-0.3, 0.05)),
                   call_wall=float(rng.uniform(-0.05, 0.3)), expiry=str(asof + dt.timedelta(days=30)),
                   earnings=(str(asof + dt.timedelta(days=20)) if rng.random() < 0.25 else None))
        return row

    import yfinance as yf
    try:
        t = yf.Ticker(tk)
        exps = [e for e in t.options if (dt.date.fromisoformat(e) - asof).days >= 7]
        if exps:
            best = min(exps, key=lambda e: abs((dt.date.fromisoformat(e) - asof).days - TARGET_DTE))
            ch = t.option_chain(best)
            row["iv"] = _atm_iv(ch.calls, ch.puts, spot)
            row["expiry"] = best
            # OI 牆：45 日內到期合約，按行使價加總未平倉量，取最大者
            near = [e for e in exps if (dt.date.fromisoformat(e) - asof).days <= OI_WINDOW_DAYS][:6] or [best]
            calls, puts = [], []
            for e in near:
                ce = ch if e == best else t.option_chain(e)
                calls.append(ce.calls[["strike", "openInterest"]])
                puts.append(ce.puts[["strike", "openInterest"]])
                time.sleep(0.3)
            co = pd.concat(calls).groupby("strike")["openInterest"].sum()
            po = pd.concat(puts).groupby("strike")["openInterest"].sum()
            if co.sum() > 0:
                row["call_wall"] = co.idxmax() / spot - 1
            if po.sum() > 0:
                row["put_wall"] = po.idxmax() / spot - 1
    except Exception as e:
        print(f"[options] {tk} 期權數據失敗：{e}")
    try:
        cal = yf.Ticker(tk).calendar
        dates = cal.get("Earnings Date", []) if isinstance(cal, dict) else []
        future = [d for d in dates if isinstance(d, dt.date) and d >= asof]
        row["earnings"] = str(min(future)) if future else None
    except Exception:
        row["earnings"] = None
    return row


def update_iv_history(rows: list[dict], asof: str, path: Path) -> None:
    """把今日 IV 存檔，再計 IV 百分位（需要累積至少 20 日數據）。"""
    new = pd.DataFrame([{"date": asof, "ticker": r["ticker"], "iv": r.get("iv")}
                        for r in rows if math.isfinite(r.get("iv", float("nan")))])
    hist = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "ticker", "iv"])
    hist = pd.concat([hist, new]).drop_duplicates(["date", "ticker"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    hist.sort_values(["ticker", "date"]).to_csv(path, index=False)
    for r in rows:
        h = hist[hist["ticker"] == r["ticker"]].sort_values("date")["iv"].tail(252)
        r["iv_n"] = len(h)
        if len(h) >= 20 and math.isfinite(r.get("iv", float("nan"))):
            r["iv_pct"] = 100 * float((h <= r["iv"]).mean())


# ================================= 輸出 HTML =================================
def f(x, d: int = 1, sign: bool = False, pct: bool = False) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    if round(x, d) == 0:
        x = 0.0
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    return s + ("%" if pct else "")


def tone(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    return "pos" if x > 0 else "neg" if x < 0 else ""


def breadth_cls(val: float, w: int) -> str:
    if not math.isfinite(val):
        return ""
    lo, hi = (10, 90) if w == 20 else (20, 80)
    if val <= lo:
        return "g2"
    if val <= 30:
        return "g1"
    if val >= hi:
        return "r2"
    if val >= 70:
        return "r1"
    return ""


def pulse_svg(t: pd.DataFrame) -> str:
    W, H, pl, pr, pt, pb = 900, 300, 52, 36, 16, 30
    n = len(t)
    mx = max(400.0, float(t["up"].max()), float(t["dn"].max()))
    mx = math.ceil(mx / 100) * 100
    ph = H - pt - pb
    zero = pt + ph / 2
    sc = (ph / 2) / mx
    slot = (W - pl - pr) / n
    bw = slot * 0.62
    s = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="每日升跌 4% 股票數目">']
    for val in (mx, 300, 0, -300, -mx):
        y = zero - val * sc
        cls = "zero" if val == 0 else "dash" if abs(val) == 300 else "grid"
        s.append(f'<line x1="{pl}" x2="{W - pr}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"/>')
        lab = f"{'+' if val > 0 else ''}{int(val)}" if val else "0"
        s.append(f'<text x="{pl - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{lab}</text>')
    for i, (d, r) in enumerate(t.iterrows()):
        x = pl + i * slot + (slot - bw) / 2
        uh, dh = r["up"] * sc, r["dn"] * sc
        uc = "g2f" if r["up"] >= 300 else "g1f"
        dc = "r2f" if r["dn"] >= 300 else "r1f"
        tip = f"{d:%m-%d} 升 {int(r['up'])} / 跌 {int(r['dn'])}"
        s.append(f'<g><title>{tip}</title>'
                 f'<rect x="{x:.1f}" y="{zero - uh:.1f}" width="{bw:.1f}" height="{max(uh, 0.5):.1f}" rx="2" class="{uc}"/>'
                 f'<rect x="{x:.1f}" y="{zero:.1f}" width="{bw:.1f}" height="{max(dh, 0.5):.1f}" rx="2" class="{dc}"/></g>')
        if i % 4 == 0 or i == n - 1:
            s.append(f'<text x="{x + bw / 2:.1f}" y="{H - 8}" text-anchor="middle" class="axis">{d:%m-%d}</text>')
        if i == n - 1:
            s.append(f'<text x="{x + bw / 2:.1f}" y="{zero - uh - 6:.1f}" text-anchor="middle" class="lab pos">{int(r["up"])}</text>')
            s.append(f'<text x="{x + bw / 2:.1f}" y="{zero + dh + 14:.1f}" text-anchor="middle" class="lab neg">{int(r["dn"])}</text>')
    s.append("</svg>")
    return "".join(s)


CSS = """
:root{--bg:#f6f7f9;--card:#ffffff;--text:#16202b;--muted:#5d6876;--line:#e3e6ea;--head:#f1f3f6;
--accent:#1d3d6b;--pos:#0b6b45;--neg:#b3261e;
--g2:#0f6b47;--g2t:#ffffff;--g1:#d3eedf;--g1t:#14532d;--r2:#a3261f;--r2t:#ffffff;--r1:#f7d4cf;--r1t:#7a1a14;
--g1f:#5fb98b;--r1f:#e3877d;--bar:#2a5591;--barbg:#e8ecf2;--tick:#6b7480;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
--bg:#0f141a;--card:#161d25;--text:#e6ebf0;--muted:#94a0ad;--line:#27313c;--head:#1c252f;
--accent:#9cbbe6;--pos:#5fd39a;--neg:#ff8a80;
--g2:#1f8a5d;--g2t:#ffffff;--g1:#173d2c;--g1t:#bff0d4;--r2:#b8352c;--r2t:#ffffff;--r1:#4a2120;--r1t:#ffcdc7;
--g1f:#3f9a6e;--r1f:#c4645b;--bar:#6f9de0;--barbg:#24303d;--tick:#b8c2cc;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0f141a;--card:#161d25;--text:#e6ebf0;--muted:#94a0ad;--line:#27313c;--head:#1c252f;
--accent:#9cbbe6;--pos:#5fd39a;--neg:#ff8a80;--g2:#1f8a5d;--g2t:#fff;--g1:#173d2c;--g1t:#bff0d4;--r2:#b8352c;--r2t:#fff;
--r1:#4a2120;--r1t:#ffcdc7;--g1f:#3f9a6e;--r1f:#c4645b;--bar:#6f9de0;--barbg:#24303d;--tick:#b8c2cc;color-scheme:dark}
*{box-sizing:border-box}
:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
body{margin:0;background:var(--bg);color:var(--text);
font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;font-size:15px;line-height:1.55}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:30px;margin:0 0 6px;letter-spacing:.01em}
h2{font-size:21px;margin:42px 0 6px;padding-top:18px;border-top:2px solid var(--text)}
h3{font-size:16px;margin:24px 0 8px}
.meta{color:var(--muted);font-size:13px}.meta b{color:var(--text);font-weight:600}
.note{color:var(--muted);font-size:13.5px;max-width:78ch;margin:4px 0 14px}
.num,td,.big{font-variant-numeric:tabular-nums}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:14px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{font-size:12.5px;color:var(--muted)}.card .big{font-size:27px;font-weight:700;line-height:1.25}
.card .s{font-size:12.5px;color:var(--muted)}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{background:var(--head);color:var(--muted);font-weight:600;font-size:12.5px;text-align:right;padding:8px 10px;white-space:nowrap;position:sticky;top:0}
td{padding:6px 10px;text-align:right;border-top:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,td.l,th.l{text-align:left}
tr.today td{font-weight:700}tr.today td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
td.g2{background:var(--g2);color:var(--g2t)}td.g1{background:var(--g1);color:var(--g1t)}
td.r2{background:var(--r2);color:var(--r2t)}td.r1{background:var(--r1);color:var(--r1t)}
td.vl{border-left:1px solid var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--muted);margin:8px 2px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
svg{width:100%;height:auto;display:block}
svg .axis{fill:var(--muted);font-size:12px}svg .lab{font-size:13px;font-weight:700}
svg .lab.pos{fill:var(--pos)}svg .lab.neg{fill:var(--neg)}
svg .grid{stroke:var(--line)}svg .zero{stroke:var(--muted);stroke-width:1}
svg .dash{stroke:var(--muted);stroke-dasharray:5 4;opacity:.7}
.g2f{fill:var(--g2)}.g1f{fill:var(--g1f)}.r2f{fill:var(--r2)}.r1f{fill:var(--r1f)}
.barc{width:150px}.bar{position:relative;height:9px;background:var(--barbg);border-radius:2px}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--bar);border-radius:2px}
.bar b{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--tick)}
.pill{display:inline-block;padding:1px 8px;border-radius:6px;font-size:12px;border:1px solid var(--line)}
.z-過熱{background:var(--r1);color:var(--r1t)}.z-偏強{background:var(--g1);color:var(--g1t)}
.z-偏弱{background:var(--barbg)}.z-超賣{background:var(--g2);color:var(--g2t)}
.ev-block{background:var(--r1);color:var(--r1t)}.ev-clear{color:var(--pos)}
.tk{font-weight:700}.sub{display:block;font-size:11.5px;color:var(--muted);font-weight:400}
footer{margin-top:40px;font-size:12.5px;color:var(--muted)}footer a{color:var(--accent)}
"""


def render(m, sec, top, opts, meta, archive) -> str:
    last, prev = m.iloc[-1], m.iloc[-2]
    day = m.index[-1]
    H = html.escape
    o = [f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
         f'<title>美股市寬監察 {day:%Y-%m-%d}</title><style>{CSS}</style></head><body><div class="wrap">']
    o.append(f'<h1>美股市寬與動能監察</h1><div class="meta">收市 <b>{day:%Y-%m-%d}</b>　'
             f'範圍 <b>美國普通股 + ADR（不含 ETF）</b>，收市 ≥ ${MIN_PRICE:.0f}、成交 ≥ {MIN_VOLUME:,} 股　'
             f'今日樣本 <b>{int(last["n"]):,}</b> 隻　數據 <b>Yahoo Finance、Nasdaq（免費，非官方）</b>　'
             f'生成於 {meta["generated"]}</div>')

    # ---------- 讀數卡 ----------
    ratio = (last["dn"] / last["up"]) if last["up"] else float("nan")
    ratio_txt = (f"跌升比 {f(ratio)} 倍" if last["dn"] > last["up"] else
                 f"升跌比 {f(last['up'] / last['dn']) if last['dn'] else '—'} 倍")
    o.append('<div class="cards">')
    o.append(f'<div class="card"><div class="k">升 4% / 跌 4%</div><div class="big">'
             f'<span class="pos">{int(last["up"])}</span> / <span class="neg">{int(last["dn"])}</span></div>'
             f'<div class="s">{ratio_txt}</div></div>')
    for w in (20, 50):
        o.append(f'<div class="card"><div class="k">% 高於 {w} 日線</div><div class="big">{f(last[f"p{w}"])}</div>'
                 f'<div class="s">前一日 {f(prev[f"p{w}"])}（{f(last[f"p{w}"] - prev[f"p{w}"], sign=True)}）</div></div>')
    for k, lab in (("spy_atr", "SPY"), ("qqq_atr", "QQQ")):
        o.append(f'<div class="card"><div class="k">{lab} 距 50 日 EMA</div><div class="big {tone(last[k])}">'
                 f'{f(last[k], 2, sign=True)}</div><div class="s">單位：14 日 ATR</div></div>')
    o.append(f'<div class="card"><div class="k">領導股指數 MLI</div><div class="big {tone(last["mli"])}">'
             f'{f(last["mli"], 2, sign=True, pct=True)}</div>'
             f'<div class="s">{int(last["mli_n"])} 隻成員，{f(last["mli_up"])}% 上升</div></div></div>')

    # ---------- 每日監察表 ----------
    o.append('<h3>每日監察表</h3><p class="note">SPY／QQQ 一欄為（收市 − 50 日 EMA）÷ 14 日 ATR。'
             'MLI 為上一交易日動力股名單在今日的平均升跌。</p><div class="tw"><table><thead><tr>'
             '<th>日期</th><th>升 4%</th><th>跌 4%</th><th>% &gt; 20</th><th>% &gt; 50</th><th>% &gt; 200</th>'
             '<th>SPY ATR</th><th>QQQ ATR</th><th>MLI %</th><th>MLI 升%</th><th>MLI N</th>'
             '<th>樣本</th><th>S&amp;P 500</th></tr></thead><tbody>')
    for i, (d, r) in enumerate(m.tail(TABLE_DAYS).iloc[::-1].iterrows()):
        if r["up"] >= r["dn"]:
            uc = "g2" if r["up"] >= 300 else "g1"
        else:
            uc = "r2" if r["dn"] >= 300 else "r1"

        def atr_c(x):
            return "r2" if x >= 5 else "g2" if x <= -5 else ""
        mc = "g1" if r["mli"] > 0 else "r1" if r["mli"] < 0 else ""
        o.append(f'<tr class="{"today" if i == 0 else ""}"><td>{d:%m-%d}</td>'
                 f'<td class="{uc}">{int(r["up"])}</td><td class="{uc}">{int(r["dn"])}</td>'
                 f'<td class="vl {breadth_cls(r["p20"], 20)}">{f(r["p20"])}</td>'
                 f'<td class="{breadth_cls(r["p50"], 50)}">{f(r["p50"])}</td>'
                 f'<td class="{breadth_cls(r["p200"], 200)}">{f(r["p200"])}</td>'
                 f'<td class="vl {atr_c(r["spy_atr"])}">{f(r["spy_atr"], 2, sign=True)}</td>'
                 f'<td class="{atr_c(r["qqq_atr"])}">{f(r["qqq_atr"], 2, sign=True)}</td>'
                 f'<td class="vl {mc}">{f(r["mli"], 2, sign=True)}</td><td>{f(r["mli_up"])}</td>'
                 f'<td>{int(r["mli_n"])}</td><td class="vl">{int(r["n"]):,}</td><td>{f(r["spx"], 2)}</td></tr>')
    o.append('</tbody></table></div><div class="legend">'
             '<span><i class="sw" style="background:var(--g2)"></i>升 4% 佔優且 ≥300</span>'
             '<span><i class="sw" style="background:var(--g1)"></i>升 4% 佔優 &lt;300</span>'
             '<span><i class="sw" style="background:var(--r1)"></i>跌 4% 佔優 &lt;300</span>'
             '<span><i class="sw" style="background:var(--r2)"></i>跌 4% 佔優且 ≥300</span>'
             '<span><i class="sw" style="background:var(--g2)"></i>% 線上極低（20 日 ≤10，50/200 日 ≤20）</span>'
             '<span><i class="sw" style="background:var(--r2)"></i>% 線上極高（20 日 ≥90，50/200 日 ≥80）</span>'
             '<span><i class="sw" style="background:var(--r2)"></i>ATR ≥ +5 過度伸展</span>'
             '<span><i class="sw" style="background:var(--g2)"></i>ATR ≤ −5 過度超賣</span></div>')

    # ---------- 4% 動量脈搏 ----------
    o.append('<h3>4% 動量脈搏</h3><p class="note">軸上為當日升 4% 或以上的股票數，軸下為跌 4% 或以上。'
             '虛線為 ±300；深色代表該方向 ≥300 隻。</p>'
             f'<div class="chart">{pulse_svg(m.tail(TABLE_DAYS))}</div>')

    # ---------- 動力股分析 ----------
    top3 = sec["share"].head(3).sum() if len(sec) else float("nan")
    best = sec.sort_values("excess", ascending=False).iloc[0] if len(sec) else None
    o.append(f'<h2>動力股分析</h2><div class="meta">條件 <b>收市 ≥ ${MIN_PRICE:.0f}、20 日平均成交額 ≥ '
             f'${LEADER_MIN_DOLLAR_VOL / 1e6:.0f}M、{LEADER_LOOKBACK} 日升幅 ≥ {LEADER_MIN_RET:.0%}</b>　'
             f'可分析股票 <b>{int(last["analysable"]):,}</b> 隻</div><div class="cards">')
    o.append(f'<div class="card"><div class="k">動力股數目</div><div class="big">{int(last["leaders"])}</div>'
             f'<div class="s">前一日 {int(prev["leaders"])}（{int(last["leaders"] - prev["leaders"]):+d}）</div></div>')
    o.append(f'<div class="card"><div class="k">佔可分析股票</div><div class="big">'
             f'{f(100 * last["leaders"] / max(last["analysable"], 1), pct=True)}</div>'
             f'<div class="s">分母 {int(last["analysable"]):,} 隻</div></div>')
    o.append(f'<div class="card"><div class="k">頭三大行業集中度</div><div class="big">{f(top3, pct=True)}</div>'
             f'<div class="s">共 {len(sec)} 個行業有動力股</div></div>')
    if best is not None:
        o.append(f'<div class="card"><div class="k">最強行業（超額最高）</div><div class="big">'
                 f'{f(best["excess"], sign=True)}pp</div><div class="s">{H(best.name)}，滲透率 '
                 f'{f(best["pen"], pct=True)}</div></div>')
    o.append('</div><div class="tw"><table><thead><tr><th class="l">行業</th><th class="l">佔比</th>'
             '<th>數量</th><th>佔比%</th><th>池內%</th><th>滲透率%</th><th>超額 pp</th></tr></thead><tbody>')
    mx = max(sec["share"].max(), sec["pool_pct"].max(), 1) if len(sec) else 1
    for name, r in sec.iterrows():
        o.append(f'<tr><td class="l">{H(str(name))}</td><td class="l barc"><div class="bar">'
                 f'<i style="width:{100 * r["share"] / mx:.1f}%"></i><b style="left:{100 * r["pool_pct"] / mx:.1f}%"></b>'
                 f'</div></td><td>{int(r["lead"])}</td><td>{f(r["share"])}</td><td>{f(r["pool_pct"])}</td>'
                 f'<td>{f(r["pen"])}</td><td class="{tone(r["excess"])}">{f(r["excess"], sign=True)}</td></tr>')
    o.append('</tbody></table></div><div class="legend"><span><i class="sw" style="background:var(--bar)"></i>'
             '藍條＝佔全部動力股百分比</span><span><i class="sw" style="background:var(--tick)"></i>'
             '刻度＝該行業在可分析股票中的佔比；藍條越過刻度即跑贏自身權重</span></div>')

    o.append('<h3>動力股：季度升幅最高 25 隻</h3><div class="tw"><table><thead><tr><th>代號</th>'
             '<th class="l">名稱</th><th class="l">行業</th><th>收市</th><th>季度升幅</th><th>今日</th>'
             '</tr></thead><tbody>')
    for _, r in top.iterrows():
        o.append(f'<tr><td class="tk">{H(r["ticker"])}</td><td class="l">{H(str(r["name"])[:38])}</td>'
                 f'<td class="l">{H(str(r["group"]))}</td><td>{f(r["close"], 2)}</td>'
                 f'<td class="pos">{f(100 * r["r63"], sign=True, pct=True)}</td>'
                 f'<td class="{tone(r["ret"])}">{f(100 * r["ret"], sign=True, pct=True)}</td></tr>')
    o.append('</tbody></table></div>')

    # ---------- 期權觀察名單 ----------
    o.append('<h2>期權觀察名單</h2><p class="note">D20／D50／D200＝現價距離 20／50／200 日均線。'
             'Put 牆／Call 牆＝45 日內到期合約中未平倉量最大的行使價距離現價。IV＝約 30 日平值引伸波幅；'
             'RV20＝20 日實際波幅；VRP20＝IV − RV20。IV 百分位需要累積 20 日或以上自己存下的 IV 才會顯示。'
             '事件：到期前有財報即標 Block。</p>'
             '<div class="tw"><table><thead><tr><th>#</th><th class="l">代號</th><th>現價</th><th>位置</th>'
             '<th>D20</th><th>D50</th><th>D200</th><th>Put 牆</th><th>Call 牆</th><th>IV%</th>'
             '<th>IV 百分位</th><th>RV20</th><th>VRP20</th><th>事件</th><th>用作 IV 的到期日</th>'
             '</tr></thead><tbody>')
    for i, r in enumerate(opts, 1):
        z = r.get("zone", "—")
        earn = r.get("earnings")
        exp = r.get("expiry")
        block = bool(earn and exp and earn <= exp)
        ev = (f'<span class="pill ev-block">Block</span><span class="sub">{earn} 財報</span>' if block
              else '<span class="pill ev-clear">Clear</span>')
        ivp = (f(r["iv_pct"]) if "iv_pct" in r else f'<span class="sub">累積中 {r.get("iv_n", 0)}/20</span>')
        vrp = r.get("iv", float("nan")) - r.get("rv20", float("nan"))
        o.append(f'<tr><td>{i}</td><td class="l tk">{H(r["ticker"])}</td><td>{f(r.get("spot"), 2)}</td>'
                 f'<td><span class="pill z-{z}">{z}</span></td>'
                 + "".join(f'<td class="{tone(r.get(k))}">{f(100 * r.get(k, float("nan")), sign=True, pct=True)}</td>'
                           for k in ("d20", "d50", "d200", "put_wall", "call_wall"))
                 + f'<td>{f(r.get("iv"))}</td><td>{ivp}</td><td>{f(r.get("rv20"))}</td>'
                 f'<td class="{tone(vrp)}">{f(vrp, 2, sign=True)}</td><td>{ev}</td><td>{exp or "—"}</td></tr>')
    o.append('</tbody></table></div><div class="legend"><span>位置按 D20 劃分：≥ +10% 過熱，+3% 至 +10% 偏強，'
             '−2% 至 +3% 中性，−10% 至 −2% 偏弱，≤ −10% 超賣</span></div>')

    # ---------- 頁尾 ----------
    o.append('<footer><p>免費數據可能有遺漏、延遲或錯誤，尤其期權 IV 在收市後由 Yahoo 計算，只作參考，並非投資建議。</p>')
    if archive:
        o.append("<p>過往頁面：" + "　".join(f'<a href="archive/{a}.html">{a}</a>' for a in archive) + "</p>")
    o.append("</footer></div></body></html>")
    return "".join(o)


# ================================= 假數據（測試用） =================================
def make_demo():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.offsets.BDay(1), periods=320)
    n = 2600
    names = [f"D{i:04d}" for i in range(n)]
    groups = rng.choice([g for g, _ in INDUSTRY_RULES] + ["Unclassified"], size=n)
    mkt = rng.normal(0.0004, 0.011, len(dates))
    beta = rng.uniform(0.5, 2.0, n)
    vol = rng.uniform(0.012, 0.045, n)
    drift = rng.normal(0.0004, 0.0015, n)
    rets = mkt[:, None] * beta + drift + rng.normal(0, 1, (len(dates), n)) * vol
    close = pd.DataFrame(np.exp(np.cumsum(rets, axis=0)) * rng.lognormal(3.3, 1.0, n), index=dates, columns=names)
    volume = pd.DataFrame(rng.lognormal(13.3, 1.1, (len(dates), n)), index=dates, columns=names)
    px = {"Close": close, "Volume": volume, "High": close * 1.01, "Low": close * 0.99}
    bc = {}
    for sym, b in (("SPY", 1.0), ("QQQ", 1.25), ("^GSPC", 1.0)):
        bc[sym] = pd.Series(np.exp(np.cumsum(mkt * b)) * (600 if sym != "^GSPC" else 6000), index=dates)
    for tk in WATCHLIST:
        bc[tk] = pd.Series(np.exp(np.cumsum(rng.normal(0.001, 0.028, len(dates)))) * rng.uniform(80, 900), index=dates)
    bcl = pd.DataFrame(bc)
    bench = {"Close": bcl, "High": bcl * 1.008, "Low": bcl * 0.992, "Volume": bcl * 0 + 1e7}
    uni = pd.DataFrame({"yahoo": names, "name": [f"Demo Company {i}" for i in range(n)], "group": groups})
    return uni, px, bench


# ================================= 主程式 =================================
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="用假數據測試版面")
    args = ap.parse_args()
    hkt = dt.timezone(dt.timedelta(hours=8))
    generated = dt.datetime.now(hkt).strftime("%Y-%m-%d %H:%M 香港時間")

    if args.demo:
        uni, px, bench = make_demo()
    else:
        uni = fetch_universe()
        px = download_prices(uni["yahoo"].tolist())
        bench = download_prices(["SPY", "QQQ", "^GSPC"] + WATCHLIST, chunk=50)

    m, leader, analysable, ret, r63 = compute_metrics(px, bench)
    day = m.index[-1]
    groups = uni.set_index("yahoo")["group"]
    names = uni.set_index("yahoo")["name"]
    sec = sector_table(leader.loc[day], analysable.loc[day], groups)

    lt = leader.loc[day]
    ids = lt[lt.fillna(False).astype(bool)].index
    top = pd.DataFrame({"ticker": ids, "name": names.reindex(ids).values,
                        "group": groups.reindex(ids).fillna("Unclassified").values,
                        "close": px["Close"].loc[day, ids].values, "r63": r63.loc[day, ids].values,
                        "ret": ret.loc[day, ids].values}).sort_values("r63", ascending=False).head(25)

    opts = []
    for tk in WATCHLIST:
        series = bench["Close"][tk] if tk in bench["Close"] else pd.Series(dtype=float)
        opts.append(option_snapshot(tk, series.loc[:day], demo=args.demo))
        if not args.demo:
            time.sleep(0.5)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.demo:
        for r in opts:
            r["iv_n"] = 0
        page = render(m, sec, top, opts, {"generated": generated}, [])
        (OUT_DIR / "demo.html").write_text(page, encoding="utf-8")
        print(f"[demo] 已輸出 {OUT_DIR / 'demo.html'}")
        return

    update_iv_history(opts, f"{day:%Y-%m-%d}", DATA_DIR / "iv_history.csv")
    hist_path = DATA_DIR / "breadth_history.csv"
    old = pd.read_csv(hist_path, index_col=0, parse_dates=True) if hist_path.exists() else pd.DataFrame()
    new = m.tail(TABLE_DAYS).round(3)
    pd.concat([old[~old.index.isin(new.index)], new]).sort_index().to_csv(hist_path)

    arch_dir = OUT_DIR / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    archive = sorted({p.stem for p in arch_dir.glob("*.html")} | {f"{day:%Y-%m-%d}"}, reverse=True)[:30]
    page = render(m, sec, top, opts, {"generated": generated}, archive)
    (OUT_DIR / "index.html").write_text(page, encoding="utf-8")
    (arch_dir / f"{day:%Y-%m-%d}.html").write_text(page.replace('href="archive/', 'href="'), encoding="utf-8")

    last = m.iloc[-1]
    print(f"[done] {day:%Y-%m-%d}　升4% {int(last['up'])}／跌4% {int(last['dn'])}　"
          f"%>50 {last['p50']:.1f}　動力股 {int(last['leaders'])}")


if __name__ == "__main__":
    main()
