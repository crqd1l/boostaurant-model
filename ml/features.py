"""Фичи клиента для k-means (cold-start): средний чек + доли категорий.

Один и тот же вектор считается и на обучении (клиент с историей), и на
инференсе (новичок с одним заказом) — иначе cold-start сломается на
рассогласовании: скейлер обучен на одном наборе колонок, а на вход придёт
другой.

Состав вектора (1 + 12 = 13 чисел):
  M — средний чек            про то, СКОЛЬКО тратит
  доли 12 категорий меню     про то, ЧТО ест

Почему доли, а не абсолютные счётчики: сырые количества сделают вектор частого
гостя большим по всем осям, и k-means сгруппирует клиентов по активности, а не
по вкусу. Доля отвечает на вопрос "какую часть своей еды клиент отдал этой
категории" — она сравнима между редким гурманом и частым бизнес-ланчем.

Почему НЕТ recency и frequency, хотя классический RFM их требует. Этот вектор
существует ради cold-start, а у новичка R и F вырождены: frequency всегда 1,
recency — дата единственного визита. Это не сигнал, а шум, причём вредный:
frequency=1 делает любого новичка похожим на гурмана (единственный архетип с
низкой частотой, 3-8 визитов), и кластер гурмана работал магнитом — в него
утекали бизнес-ланчи, мясоеды и вегетарианцы.

Замер на данных, попадание новичка в свой кластер:
                        1 визит   2 визита   3 визита
  RFM + категории        69.7%     75.3%      78.3%
  чек + категории        79.0%     90.3%      95.3%   <- выбрано

monetary остаётся: сумма единственного чека — осмысленная величина, в отличие
от "частоты = 1". Она и разводит гурмана (5800) со сладкоежкой (920).

customers.archetype сюда НЕ попадает — это была бы утечка. k-means должен
восстановить сегменты по одному лишь поведению; archetype нужен только чтобы
потом проверить, получилось ли (см. train_kmeans.py).
"""

import pandas as pd
from sqlalchemy import text

from data_gen.archetypes import CATEGORIES
from db.connection import get_engine

# Порядок колонок фиксирован: скейлер и KMeans запоминают позиции, а не имена.
FEATURE_COLUMNS = ["monetary"] + [f"cat_{c}" for c in CATEGORIES]

# Средний чек по всем клиентам с историей.
RFM_SQL = """
    SELECT o.customer_id::text  AS customer_id,
           AVG(o.total_sum)     AS monetary
    FROM orders o
    GROUP BY o.customer_id
"""

# Количество позиций по категориям — из них считаем доли.
CATEGORY_SQL = """
    SELECT o.customer_id::text AS customer_id,
           m.category          AS category,
           SUM(oi.quantity)    AS qty
    FROM order_items oi
    JOIN orders     o ON o.id = oi.order_id
    JOIN menu_items m ON m.id = oi.menu_item_id
    GROUP BY o.customer_id, m.category
"""

# То же самое, но для одного клиента (инференс cold-start).
RFM_ONE_SQL = RFM_SQL.replace("GROUP BY", "WHERE o.customer_id = :cid\n    GROUP BY")
CATEGORY_ONE_SQL = CATEGORY_SQL.replace("GROUP BY", "WHERE o.customer_id = :cid\n    GROUP BY")


def _assemble(rfm: pd.DataFrame, cats: pd.DataFrame) -> pd.DataFrame:
    """Склеивает RFM и доли категорий в матрицу фич с фиксированным порядком колонок."""
    # Длинная таблица (customer, category, qty) → широкая (customer × category).
    wide = cats.pivot(index="customer_id", columns="category", values="qty").fillna(0.0)

    # Категории, которых клиент не пробовал, всё равно должны быть колонками —
    # иначе размерность вектора поедет и скейлер упадёт.
    for c in CATEGORIES:
        if c not in wide.columns:
            wide[c] = 0.0
    wide = wide[CATEGORIES]

    totals = wide.sum(axis=1).replace(0.0, 1.0)  # деление на ноль у клиента без позиций
    shares = wide.div(totals, axis=0)
    shares.columns = [f"cat_{c}" for c in shares.columns]

    df = rfm.set_index("customer_id").join(shares, how="left").fillna(0.0)
    return df[FEATURE_COLUMNS].astype(float)


def all_customer_features() -> pd.DataFrame:
    """Фичи всех клиентов с историей. Индекс — customer_id."""
    with get_engine().connect() as conn:
        rfm = pd.read_sql(text(RFM_SQL), conn)
        cats = pd.read_sql(text(CATEGORY_SQL), conn)
    if rfm.empty:
        raise SystemExit("orders пуст — сначала прогони data_gen.generate")
    return _assemble(rfm, cats)


def customer_features(customer_id: str) -> pd.DataFrame | None:
    """Фичи одного клиента. None — если заказов нет вообще (тогда popularity-fallback).

    Работает и для новичка с единственным визитом: R и F тривиальны, M — сумма
    того заказа, доли категорий — из его позиций. Именно это делает cold-start
    возможным.
    """
    with get_engine().connect() as conn:
        rfm = pd.read_sql(text(RFM_ONE_SQL), conn, params={"cid": customer_id})
        cats = pd.read_sql(text(CATEGORY_ONE_SQL), conn, params={"cid": customer_id})
    if rfm.empty:
        return None
    return _assemble(rfm, cats)
