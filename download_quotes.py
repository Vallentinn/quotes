"""
Скачивание дневных котировок в папку quotes/.
Формат CSV как экспорт MultiCharts: дата EasyLanguage (JJJMMTT), время, O, H, L, C — без заголовка.

Два списка:
  HISTORY   — американские ETF, длинная история, нужны для бэктестов
  PORTFOLIO — UCITS-фонды, которые реально покупаются в ЕС

Для каждого инструмента задано несколько тикеров-кандидатов: скрипт пробует их
по очереди и берёт первый, который отдаёт данные. Так список переживает
переименования и отсутствие отдельных листингов на Yahoo.
"""
from pathlib import Path

import pandas as pd
import yfinance as yf

# имя файла: список тикеров-кандидатов (пробуются по порядку)
HISTORY = {
    "SPY":  ["SPY"],
    "QQQ":  ["QQQ"],
    "DIA":  ["DIA"],
    "IWM":  ["IWM"],
    "DAX":  ["^GDAXI"],
    "N225": ["^N225"],
    "EEM":  ["EEM"],
    "GLD":  ["GLD"],
    "TLT":  ["TLT"],
    "IEF":  ["IEF"],
    "DBC":  ["DBC"],
}

PORTFOLIO = {
    # итоговый состав, все бумаги проверены в Trade Republic
    "SXR8": ["SXR8.DE", "CSPX.L"],             # IE00B5BMR087  S&P 500
    "SXRV": ["SXRV.DE", "CNDX.L"],             # IE00B53SZB19  Nasdaq 100
    "XRS2": ["XRS2.DE", "XRSU.L"],             # IE00BJZ2DD79  Russell 2000
    "EXS1": ["EXS1.DE"],                       # DE0005933931  DAX
    "XDJP": ["XDJP.DE", "XDJP.L"],             # LU0839027447  Nikkei 225
    "IS3N": ["IS3N.DE", "EIMI.L"],             # IE00BKM4GZ66  развивающиеся рынки
    "EGLN": ["EGLN.DE", "SGLN.L", "IGLN.L"],   # IE00B4ND3602  золото
    "EXXY": ["EXXY.DE"],                       # DE000A0H0728  сырьё
    "SXRC": ["SXRC.DE", "DTLA.L"],             # IE00BFM6TC58  US Treasuries 20+
    "XUTD": ["XUTD.DE", "XUTD.L"],             # LU0429459356  US Treasuries широкие
}

START = "1990-01-01"
OUT_DIR = Path(__file__).parent / "quotes"
OUT_DIR.mkdir(exist_ok=True)


def to_easylanguage_date(ts):
    """2026-09-16 -> 1260916 (год минус 1900, как в MultiCharts)"""
    return (ts.year - 1900) * 10000 + ts.month * 100 + ts.day


def fetch(tickers):
    """Пробует тикеры по очереди, возвращает первый непустой результат"""
    for ticker in tickers:
        try:
            df = yf.download(ticker, start=START, auto_adjust=False, progress=False)
        except Exception as err:                      # сеть, лимит запросов и т.п.
            print(f"    {ticker}: ошибка запроса ({err})")
            continue
        if df is None or df.empty:
            print(f"    {ticker}: пусто")
            continue
        if isinstance(df.columns, pd.MultiIndex):     # новые версии yfinance
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close"]].dropna()
        if len(df) < 30:
            print(f"    {ticker}: слишком мало баров ({len(df)})")
            continue
        return df, ticker
    return None, ""


def save(name, tickers):
    df, used = fetch(tickers)
    if df is None:
        print(f"{name:6s} НЕ СКАЧАН — проверь тикеры {tickers}")
        return False

    flat = int((df["High"] == df["Low"]).sum())
    broken = int(((df["High"] < df[["Open", "Close"]].max(axis=1)) |
                  (df["Low"] > df[["Open", "Close"]].min(axis=1))).sum())

    out = pd.DataFrame({
        "date": [to_easylanguage_date(d) for d in df.index],
        "time": 1600,
        "open": df["Open"].round(4),
        "high": df["High"].round(4),
        "low": df["Low"].round(4),
        "close": df["Close"].round(4),
    })
    out.to_csv(OUT_DIR / f"{name}.csv", header=False, index=False)
    print(f"{name:6s} {used:10s} {df.index[0].date()} — {df.index[-1].date()}  "
          f"{len(df):6d} баров | High=Low: {flat} | ошибки OHLC: {broken}")
    return True


def main():
    failed = []
    for title, group in [("ИСТОРИЯ (US ETF)", HISTORY), ("ПОРТФЕЛЬ (UCITS)", PORTFOLIO)]:
        print(f"\n=== {title} ===")
        for name, tickers in group.items():
            if not save(name, tickers):
                failed.append(name)
    print("\nГотово. Файлы в", OUT_DIR.resolve())
    if failed:
        print("Не скачаны:", ", ".join(failed))


if __name__ == "__main__":
    main()
