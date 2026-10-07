"""
Проверка качества котировок в папке quotes/.

Запускается после download_quotes.py. Ничего не исправляет — только сообщает.
Если найдены проблемы:
  - печатает отчёт в консоль,
  - записывает его в check_report.md,
  - завершается с кодом 1 (GitHub Action по этому коду создаёт Issue).

Проверки для каждого файла:
  1. Даты: идут по возрастанию, без повторов.
  2. Свежесть: последний бар не отстаёт от самого свежего файла больше чем на MAX_LAG_DAYS.
  3. OHLC: цены > 0, High >= Low, Close внутри [Low, High].
  4. Выбросы: Modified Z-Score (через MAD) дневной доходности
     относительно предыдущих WINDOW дней.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QUOTES_DIR = Path(__file__).parent / "quotes"
REPORT_FILE = Path(__file__).parent / "check_report.md"

CHECK_DAYS = 10      # сколько последних баров проверять (для разовой проверки всей истории поставь 100000)
WINDOW = 252         # по скольким предыдущим дням считаем "нормальный" разброс
THRESHOLD = 10.0     # порог |Mz|: выше — бар подозрительный
MIN_MOVE = 0.03      # выбросом считаем только движения больше 3 % (иначе на вялых данных MAD≈0 и всё "выброс")
CLOSE_TOLERANCE = 0.001  # допуск 0,1 %: Close у валют Yahoo часто чуть вылезает за High/Low
MAX_LAG_DAYS = 5     # допустимое отставание последней даты, в календарных днях


def read_quotes(path):
    """Читает CSV в формате MultiCharts и возвращает таблицу с датой-индексом."""
    df = pd.read_csv(path, header=None,
                     names=["date", "time", "open", "high", "low", "close"])
    # 1261006 -> год 126+1900=2026, месяц 10, день 06
    d = df["date"]
    df.index = pd.to_datetime(dict(year=d // 10000 + 1900, month=d // 100 % 100, day=d % 100))
    return df[["open", "high", "low", "close"]]


def mad_score(window_values):
    """
    Mz последнего значения в окне относительно всех предыдущих.
    Mz = 0.6745 * (x - медиана) / MAD, где MAD = медиана |x_i - медиана|.
    Сам проверяемый день в "норму" не входит, чтобы выброс не размывал порог.
    """
    past, x = window_values[:-1], window_values[-1]
    med = np.median(past)
    mad = np.median(np.abs(past - med))
    if mad == 0:                # плоские цены — делить не на что
        return np.nan
    return 0.6745 * (x - med) / mad


def modified_z(returns, window):
    """Mz для каждого дня: окно = window предыдущих дней + сам день."""
    return returns.rolling(window + 1, min_periods=window // 2).apply(mad_score, raw=True)


def check_file(path, newest_date):
    """Возвращает список строк с проблемами для одного файла."""
    problems = []
    df = read_quotes(path)

    # 1. Даты
    if not df.index.is_monotonic_increasing:
        problems.append("даты идут не по порядку")
    dups = df.index[df.index.duplicated()]
    if len(dups):
        problems.append(f"повторяющиеся даты: {', '.join(str(x.date()) for x in dups[:5])}")

    # 2. Свежесть
    lag = (newest_date - df.index[-1]).days
    if lag > MAX_LAG_DAYS:
        problems.append(f"данные устарели: последний бар {df.index[-1].date()} (отставание {lag} дн.)")

    # 3. OHLC только по последним CHECK_DAYS барам
    recent = df.tail(CHECK_DAYS)
    bad = recent[(recent <= 0).any(axis=1)
                 | (recent["high"] < recent["low"])
                 | (recent["close"] > recent["high"] * (1 + CLOSE_TOLERANCE))
                 | (recent["close"] < recent["low"] * (1 - CLOSE_TOLERANCE))]
    for day, row in bad.iterrows():
        problems.append(f"{day.date()}: некорректный бар O={row.open} H={row.high} L={row.low} C={row.close}")

    # 4. Выбросы по Modified Z-Score
    returns = np.log(df["close"]).diff().dropna()
    returns = returns.tail(CHECK_DAYS + WINDOW)     # для расчёта хватит хвоста
    mz = modified_z(returns, WINDOW).tail(CHECK_DAYS)
    suspicious = mz[(mz.abs() > THRESHOLD) & (returns.abs() > MIN_MOVE)]
    for day, value in suspicious.items():
        i = df.index.get_loc(day)
        prev, cur = df["close"].iloc[i - 1], df["close"].iloc[i]
        text = (f"{day.date()}: выброс Mz={value:+.1f}, close {prev} -> {cur} "
                f"({(cur / prev - 1) * 100:+.1f} %)")
        # Признак битого бара: на следующий день цена возвращается обратно
        if i + 1 < len(df):
            r_now = returns.loc[day]
            r_next = np.log(df["close"].iloc[i + 1] / cur)
            if r_now * r_next < 0 and abs(r_next) > 0.7 * abs(r_now):
                text += " — откат на следующий день: проверь, рынок это или битый бар"
        problems.append(text)
    return problems


def main():
    files = sorted(p for p in QUOTES_DIR.glob("*.csv") if not p.name.startswith("_"))
    newest = max(read_quotes(p).index[-1] for p in files)

    report = {}
    for path in files:
        problems = check_file(path, newest)
        status = "OK" if not problems else f"{len(problems)} проблем(ы)"
        print(f"{path.stem:7s} {status}")
        if problems:
            report[path.stem] = problems

    if not report:
        print("\nВсе файлы в порядке.")
        REPORT_FILE.unlink(missing_ok=True)
        return 0

    lines = [f"Проверка котировок: найдены проблемы (последние {CHECK_DAYS} баров, порог |Mz| > {THRESHOLD})", ""]
    for name, problems in report.items():
        lines.append(f"**{name}**")
        lines += [f"- {p}" for p in problems]
        lines.append("")
    text = "\n".join(lines)
    REPORT_FILE.write_text(text, encoding="utf-8")
    print("\n" + text)
    return 1


if __name__ == "__main__":
    sys.exit(main())
