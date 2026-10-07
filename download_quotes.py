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

# курсы для пересчёта стоимости позиций в евро (сколько валюты за 1 EUR)
FX = {
    "FX_USD": ["EURUSD=X"],
    "FX_GBP": ["EURGBP=X"],
}

START = "1990-01-01"
OUT_DIR = Path(__file__).parent / "quotes"
OUT_DIR.mkdir(exist_ok=True)


def to_easylanguage_date(ts):
    """2026-09-16 -> 1260916 (год минус 1900, как в MultiCharts)"""
    return (ts.year - 1900) * 10000 + ts.month * 100 + ts.day


def exchange_closed(ticker, now):
    """Закрылась ли сегодня биржа тикера (время UTC с запасом, летом и зимой)"""
    minutes = now.hour * 60 + now.minute
    if ticker.endswith("=X"):                                   # валюты торгуются круглосуточно
        return False
    if ticker == "^N225":                                       # Токио закрывается около 06:30 UTC
        return minutes >= 7 * 60
    if ticker == "^GDAXI" or ticker.endswith((".DE", ".F", ".AS", ".PA", ".L")):
        return minutes >= 17 * 60 + 45                          # Xetra и Лондон — до 16:30 UTC
    return minutes >= 21 * 60 + 30                              # США — до 21:00 UTC


def merge_with_existing(name, used, df):
    """Не теряем бары, которых Yahoo временно не отдаёт: старые строки файла сохраняются,
    новые данные заменяют их на совпадающих датах. Если сменился тикер (другой листинг,
    другая валюта), файл перезаписывается целиком."""
    path = OUT_DIR / f"{name}.csv"
    meta = OUT_DIR / "_meta.csv"
    if not path.exists():
        return df
    if meta.exists():
        prev = pd.read_csv(meta).set_index("name")["ticker"]
        if name in prev and prev[name] != used:
            print(f"    {name}: тикер сменился ({prev[name]} -> {used}), файл перезаписан")
            return df
    old = pd.read_csv(path, header=None, names=["d", "t", "Open", "High", "Low", "Close"])
    d = old["d"]
    old.index = pd.to_datetime(dict(year=d // 10000 + 1900, month=d // 100 % 100, day=d % 100))
    old = old[["Open", "High", "Low", "Close"]]
    kept = old[~old.index.isin(df.index) & (old.index > df.index[0])]
    if len(kept):
        print(f"    {name}: сохранено {len(kept)} бар(ов), которых нет в ответе Yahoo: "
              f"{', '.join(str(x.date()) for x in kept.index[-3:])}")
    return pd.concat([df, kept]).sort_index()


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
        # только закрытые сессии: сегодняшний бар оставляем, лишь если биржа уже закрылась
        now = pd.Timestamp.now(tz="UTC")
        today = now.normalize().tz_localize(None)
        if not exchange_closed(ticker, now):
            df = df[df.index < today]
        if len(df) < 30:
            print(f"    {ticker}: слишком мало баров ({len(df)})")
            continue
        return df, ticker
    return None, ""


def currency_of(ticker):
    """Валюта котировки: EUR, USD, GBp (пенсы) ... ; по суффиксу, если Yahoo не ответил"""
    try:
        cur = yf.Ticker(ticker).fast_info.get("currency")
        if cur:
            return cur
    except Exception:
        pass
    return {".DE": "EUR", ".F": "EUR", ".AS": "EUR", ".PA": "EUR"}.get(ticker[-3:], "USD")


META = []            # имя файла, использованный тикер, валюта


def save(name, tickers, with_currency=False):
    df, used = fetch(tickers)
    if df is None:
        print(f"{name:6s} НЕ СКАЧАН — проверь тикеры {tickers}")
        return False
    if with_currency:
        META.append((name, used, currency_of(used)))
    df = merge_with_existing(name, used, df)

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
            if not save(name, tickers, with_currency=(group is PORTFOLIO)):
                failed.append(name)
    print("\n=== КУРСЫ ВАЛЮТ ===")
    for name, tickers in FX.items():
        if not save(name, tickers):
            failed.append(name)
    pd.DataFrame(META, columns=["name", "ticker", "currency"]).to_csv(OUT_DIR / "_meta.csv", index=False)
    print("\nВалюты листингов:", ", ".join(f"{n}={c}" for n, _, c in META))
    print("\nГотово. Файлы в", OUT_DIR.resolve())
    if failed:
        print("Не скачаны:", ", ".join(failed))


if __name__ == "__main__":
    main()
