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

# кандидат в «ядро» портфеля (просто держать): MSCI World, накопительный, в EUR
CORE = {
    "WORLD": ["EUNL.DE", "IWDA.AS"],            # IE00B4L5Y983  iShares Core MSCI World
}

# курсы для пересчёта стоимости позиций в евро (сколько валюты за 1 EUR)
FX = {
    "FX_USD": ["EURUSD=X"],
    "FX_GBP": ["EURGBP=X"],
    "FX_JPY": ["EURJPY=X"],     # для пересчёта N225 в бэктесте
}

# ставка ЕЦБ по депозитам (Deposit Facility Rate), % годовых — для дохода на свободные деньги
ECB_RATE_URL = ("https://data-api.ecb.europa.eu/service/data/"
                "FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata")

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
        raw_path = OUT_DIR / f"_raw_{name}.csv"          # фактические цены сохранённых баров
        if "Raw" in df and raw_path.exists():
            r = pd.read_csv(raw_path, parse_dates=["date"]).set_index("date")["close"]
            kept = kept.assign(Raw=r.reindex(kept.index))
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
        df = df[["Open", "High", "Low", "Close", "Adj Close"]].dropna()
        # Полная доходность: Yahoo в Close не учитывает дивиденды и купоны (только сплиты),
        # Adj Close учитывает. Пересчитываем все цены бара на коэффициент Adj Close / Close,
        # последний бар при этом не меняется (корректировка идёт назад по истории).
        k = df["Adj Close"] / df["Close"]
        raw = df["Close"].copy()                    # фактическая цена — для оценки счёта
        df = df[["Open", "High", "Low", "Close"]].mul(k, axis=0)
        df["Raw"] = raw
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


def save(name, tickers, with_currency=False, raw=False):
    """raw=True — дополнительно сохранить фактические (нескорректированные) цены закрытия
    в quotes/_raw_<name>.csv: по ним считается стоимость счёта и качество исполнения.
    Скорректированные цены (с дивидендами) нужны для сигналов и бэктеста."""
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
    if raw and "Raw" in df:
        r = df["Raw"].dropna()
        pd.DataFrame({"date": r.index.strftime("%Y-%m-%d"), "close": r.round(4).values}) \
            .to_csv(OUT_DIR / f"_raw_{name}.csv", index=False)
    print(f"{name:6s} {used:10s} {df.index[0].date()} — {df.index[-1].date()}  "
          f"{len(df):6d} баров | High=Low: {flat} | ошибки OHLC: {broken}")
    return True


def save_ecb_rate():
    """Ставка ЕЦБ по депозитам -> quotes/_rate_eur.csv (date,rate в % годовых).
    Имя с «_» — чтобы check_quotes.py не проверял файл как котировки.
    Если ЕЦБ не ответил, старый файл остаётся как есть."""
    try:
        df = pd.read_csv(ECB_RATE_URL)
        out = df[["TIME_PERIOD", "OBS_VALUE"]].dropna()
        out.columns = ["date", "rate"]
        out = out.sort_values("date")
        out.to_csv(OUT_DIR / "_rate_eur.csv", index=False)
        print(f"ECB    DFR        {out['date'].iloc[0]} — {out['date'].iloc[-1]}  "
              f"последняя ставка {out['rate'].iloc[-1]:.2f}%")
        return True
    except Exception as err:
        print(f"ECB    ставка НЕ СКАЧАНА ({err}) — остаётся прежний файл")
        return False


def main():
    failed = []
    for title, group in [("ИСТОРИЯ (US ETF)", HISTORY), ("ПОРТФЕЛЬ (UCITS)", PORTFOLIO),
                         ("ЯДРО", CORE)]:
        print(f"\n=== {title} ===")
        for name, tickers in group.items():
            if not save(name, tickers, with_currency=(group is PORTFOLIO),
                        raw=(group is not HISTORY)):
                failed.append(name)
    print("\n=== КУРСЫ ВАЛЮТ ===")
    for name, tickers in FX.items():
        if not save(name, tickers):
            failed.append(name)
    print("\n=== СТАВКА ЕЦБ ===")
    if not save_ecb_rate():
        failed.append("ECB")
    pd.DataFrame(META, columns=["name", "ticker", "currency"]).to_csv(OUT_DIR / "_meta.csv", index=False)
    print("\nВалюты листингов:", ", ".join(f"{n}={c}" for n, _, c in META))
    print("\nГотово. Файлы в", OUT_DIR.resolve())
    if failed:
        print("Не скачаны:", ", ".join(failed))


if __name__ == "__main__":
    main()
