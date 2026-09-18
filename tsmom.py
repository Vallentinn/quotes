"""
Трендовый портфель (time-series momentum).

Два режима:
  * бэктест на американских ETF (длинная история) — HISTORY
  * текущие веса по UCITS-фондам, которые реально покупаются — PORTFOLIO

Правила (пересчёт в последний торговый день месяца):
  сигнал  = среднее знаков доходности за 21, 63 и 252 дня; минус -> 0 (без шортов)
  вес     = сигнал × (0,15 / корень из числа инструментов) / волатильность (63 дня)
  потолок = не больше WEIGHT_CAP на инструмент
  итого   = не больше 100% (без плеча), остаток — деньги
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "quotes"
HISTORY = ["SPY", "QQQ", "DIA", "IWM", "DAX", "N225", "EEM", "GLD", "TLT", "IEF", "DBC"]
PORTFOLIO = ["SXR8", "SXRV", "XRS2", "EXS1", "XDJP", "IS3N", "4GLD", "EXXY", "DTLE", "IBTM"]

COST = 0.0005          # 0,05% от оборота
TARGET_VOL = 0.15
WEIGHT_CAP = 0.20      # потолок на один инструмент
CAPITAL = 30000        # для пересчёта весов в евро


def load_close(name):
    d = pd.read_csv(DATA_DIR / f"{name}.csv", header=None, names=["d", "t", "o", "h", "l", "c"])
    d.index = pd.to_datetime((d["d"] + 19000000).astype(str), format="%Y%m%d")
    s = d["c"]
    if name == "DAX":                      # до 1994 у Yahoo только цены закрытия
        s = s["1994":]
    return s.rename(name)


def load_group(names):
    have = [n for n in names if (DATA_DIR / f"{n}.csv").exists()]
    missing = [n for n in names if n not in have]
    if missing:
        print("ВНИМАНИЕ: нет файлов", missing)
    return pd.concat([load_close(n) for n in have], axis=1, sort=True)


def weights(P, lookbacks=(21, 63, 252)):
    """Веса на каждый день (до ограничения общей суммы)"""
    R = P.pct_change(fill_method=None).fillna(0)
    Pf = P.ffill()
    vol = R.rolling(63).std() * np.sqrt(252)
    sig = sum(np.sign(Pf / Pf.shift(lb) - 1) for lb in lookbacks) / len(lookbacks)
    sig = sig.clip(lower=0)
    raw = (sig * (TARGET_VOL / np.sqrt(P.shape[1])) / vol).where(Pf.notna())
    return raw.clip(upper=WEIGHT_CAP), sig, vol, R


def backtest(P, start):
    raw, _, _, R = weights(P)
    dates = R.index.to_series()
    month_end = dates.groupby(R.index.to_period("M")).transform("max") == dates
    w = raw[month_end].reindex(R.index).ffill().fillna(0)
    w = w.div(np.maximum(w.sum(axis=1), 1), axis=0)          # без плеча
    turnover = (w - w.shift(1)).abs().sum(axis=1).fillna(0)
    return ((w.shift(1).fillna(0) * R).sum(axis=1) - turnover * COST)[start:], w.shift(1)[start:]


def stats(r, name):
    e = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    cagr = e.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(len(r) / yrs)
    return {"Стратегия": name, "CAGR %": cagr * 100, "Волат. %": vol * 100,
            "Просадка %": (e / e.cummax() - 1).min() * 100, "CAGR/Vol": cagr / vol}


def show_backtest(start="2007-01-02"):
    P = load_group(HISTORY)
    R = P.pct_change(fill_method=None)
    avail = P.notna()
    bh = (R.where(avail).sum(axis=1) / avail.sum(axis=1)).fillna(0)[start:]
    r, w = backtest(P, start)
    rows = [stats(R["SPY"].fillna(0)[start:], "Удержание SPY"),
            stats(bh, "Удержание, равные доли"),
            stats(r, "Трендовый портфель")]
    print(f"\n=== БЭКТЕСТ (US ETF) с {start} ===")
    print(pd.DataFrame(rows).set_index("Стратегия").round(2).to_string())
    print(f"средняя экспозиция {w.sum(axis=1).mean():.0%}")
    for name, a, b in [("2008", "2007-10-09", "2009-03-09"), ("COVID", "2020-02-19", "2020-03-23"),
                       ("2022", "2022-01-03", "2022-10-12")]:
        f = lambda x: ((1 + x[a:b]).prod() - 1) * 100
        print(f"  {name:6s} SPY {f(R['SPY'].fillna(0)):6.1f}%   тренд {f(r):6.1f}%")


def show_portfolio():
    P = load_group(PORTFOLIO)
    raw, sig, vol, _ = weights(P)
    last = raw.index[-1]
    w = raw.loc[last] / max(raw.loc[last].sum(), 1)
    Pf = P.ffill()
    t = pd.DataFrame({
        "1 мес %": (Pf.iloc[-1] / Pf.shift(21).iloc[-1] - 1) * 100,
        "3 мес %": (Pf.iloc[-1] / Pf.shift(63).iloc[-1] - 1) * 100,
        "12 мес %": (Pf.iloc[-1] / Pf.shift(252).iloc[-1] - 1) * 100,
        "сигнал": sig.loc[last],
        "волат. %": vol.loc[last] * 100,
        "вес %": w * 100,
        "евро": (w * CAPITAL).round(0),
    }).sort_values("вес %", ascending=False)
    print(f"\n=== ВЕСА НА {last.date()} (UCITS) ===")
    print(t.round(2).to_string())
    print(f"в рынке {w.sum():.1%}, деньги {(1 - w.sum()) * CAPITAL:,.0f} EUR")


if __name__ == "__main__":
    show_backtest()
    show_portfolio()
