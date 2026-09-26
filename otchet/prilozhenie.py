"""Сборка приложения А к отчёту: дамп базы данных цифровой платформы.

    docker compose up -d db
    python otchet/prilozhenie.py        → otchet/Приложение_А_база_данных.sql

Выгрузка выполняется pg_dump и содержит структуру таблиц и все данные.
Телефоны гостей в таблице customers заменяются маской: данные синтетические,
но по формату являются персональными, а в разделе 1.2 отчёта заявлена
минимизация их обработки. Остальные поля выгружаются без изменений —
дата рождения без имени и телефона гостя не идентифицирует.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "Приложение_А_база_данных.sql"

HEADER = """\
-- ПРИЛОЖЕНИЕ А
-- База данных цифровой платформы предиктивной персонализации
-- клиентского обслуживания в общепите (Boostaurant)
--
-- Выгрузка содержит описание структуры таблиц и данные прототипа.
-- СУБД: PostgreSQL 16. Выгрузка выполнена средствами pg_dump.
--
-- Состав данных:
{counts}
--
-- Телефоны гостей в таблице customers заменены маской +7XXXXXXNNNN:
-- данные являются синтетическими и сформированы генератором, однако
-- по формату относятся к персональным. Связи между таблицами
-- выполняются по идентификаторам UUID и маскировкой не затронуты.

"""


def compose(*args, **kw):
    out = subprocess.run(["docker", "compose", *args], cwd=ROOT.parent,
                         capture_output=True, text=True, **kw)
    if out.returncode:
        raise SystemExit(f"docker compose {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def main():
    counts = compose("exec", "-T", "db", "psql", "-U", "boostaurant",
                     "-d", "boostaurant", "-t", "-A", "-F", "\t", "-c",
                     "SELECT relname, n_live_tup FROM pg_stat_user_tables "
                     "ORDER BY relname")
    rows = [l.split("\t") for l in counts.strip().split("\n")]
    counts_txt = "\n".join(f"--   {name} — {n} записей" for name, n in rows)

    dump = compose("exec", "-T", "db", "pg_dump", "-U", "boostaurant",
                   "-d", "boostaurant", "--inserts")

    # Маска ставится только во второй позиции INSERT-а по customers — это phone.
    # Номер обезличивается, но остаётся уникальным: на phone стоит UNIQUE,
    # и одинаковая маска у 300 гостей не даёт восстановить выгрузку.
    seq = iter(range(1, 10_000))
    masked, n = re.subn(
        r"(INSERT INTO public\.customers VALUES \('[0-9a-f-]+', )'\+7\d+'",
        lambda m: f"{m.group(1)}'+7XXXXXX{next(seq):04d}'", dump)

    OUTPUT.write_text(HEADER.format(counts=counts_txt) + masked, encoding="utf-8")
    print(f"Телефонов замаскировано: {n}")
    print(f"Строк в выгрузке: {masked.count(chr(10))}")
    print(f"Готово: {OUTPUT}  ({OUTPUT.stat().st_size / 1024:.0f} КБ)")


if __name__ == "__main__":
    main()
