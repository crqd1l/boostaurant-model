"""Профиль гостя для экрана «Инфо о посетителе» и запись заметок официанта.

Имени в БД нет намеренно (см. data_model_plan.md): для ML оно бесполезно, а в
проде придёт из iiko customer/info. Поэтому официанту показываем то, что у нас
действительно есть и что помогает в обслуживании: сегмент лояльности, давность,
частоту, средний чек, любимые блюда и — главное — аллергены.
"""

from datetime import date

from sqlalchemy import text

from db.connection import get_engine

# Сводка по гостю: RFM-подобные метрики + профиль.
PROFILE_SQL = """
    SELECT c.id::text                                   AS customer_id,
           c.phone                                      AS phone,
           c.gender                                     AS gender,
           c.birthday                                   AS birthday,
           c.loyalty_tier                               AS loyalty_tier,
           c.first_order_date                           AS first_order_date,
           COUNT(o.id)                                  AS visits,
           COALESCE(ROUND(AVG(o.total_sum)), 0)         AS avg_check,
           MAX(o.created_at)::date                      AS last_visit
    FROM customers c
    LEFT JOIN orders o ON o.customer_id = c.id
    WHERE c.id = :cid
    GROUP BY c.id
"""

# Что гость заказывает чаще всего — официанту это важнее любых метрик.
FAVORITES_SQL = """
    SELECT m.name AS name, m.category AS category, SUM(oi.quantity) AS qty
    FROM order_items oi
    JOIN orders     o ON o.id = oi.order_id
    JOIN menu_items m ON m.id = oi.menu_item_id
    WHERE o.customer_id = :cid
    GROUP BY m.name, m.category
    ORDER BY qty DESC
    LIMIT :limit
"""

PREFS_SQL = """
    SELECT allergens, dislikes, note, updated_at
    FROM customer_preferences
    WHERE customer_id = :cid
"""

UPSERT_PREFS_SQL = """
    INSERT INTO customer_preferences (customer_id, allergens, dislikes, note, updated_at)
    VALUES (:cid, :allergens, :dislikes, :note, now())
    ON CONFLICT (customer_id) DO UPDATE SET
        -- Объединяем, а не перезатираем: официант дописывает наблюдения к
        -- существующим, а не стирает то, что отметил коллега в прошлый визит.
        allergens  = ARRAY(SELECT DISTINCT unnest(
                         customer_preferences.allergens || EXCLUDED.allergens)),
        dislikes   = ARRAY(SELECT DISTINCT unnest(
                         customer_preferences.dislikes || EXCLUDED.dislikes)),
        note       = COALESCE(EXCLUDED.note, customer_preferences.note),
        updated_at = now()
    RETURNING allergens, dislikes, note
"""


def _age(birthday: date | None) -> int | None:
    if not birthday:
        return None
    today = date.today()
    return today.year - birthday.year - (
        (today.month, today.day) < (birthday.month, birthday.day)
    )


def get_profile(customer_id: str, favorites: int = 3) -> dict | None:
    """Всё, что знаем о госте. None — такого клиента нет."""
    with get_engine().connect() as conn:
        row = conn.execute(text(PROFILE_SQL), {"cid": customer_id}).mappings().first()
        if not row:
            return None

        favs = [
            dict(r) for r in
            conn.execute(text(FAVORITES_SQL), {"cid": customer_id, "limit": favorites}).mappings()
        ]
        prefs = conn.execute(text(PREFS_SQL), {"cid": customer_id}).mappings().first()

    return {
        "customer_id": row["customer_id"],
        "phone": row["phone"],
        "gender": row["gender"],
        "age": _age(row["birthday"]),
        "birthday": row["birthday"],
        "loyalty_tier": row["loyalty_tier"],
        "first_order_date": row["first_order_date"],
        "last_visit": row["last_visit"],
        "visits": int(row["visits"]),
        "avg_check": float(row["avg_check"]),
        "favorites": [{"name": f["name"], "category": f["category"], "qty": int(f["qty"])}
                      for f in favs],
        "allergens": list(prefs["allergens"]) if prefs else [],
        "dislikes": list(prefs["dislikes"]) if prefs else [],
        "note": prefs["note"] if prefs else None,
    }


def save_preferences(
    customer_id: str,
    allergens: list[str] | None = None,
    dislikes: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Записать наблюдение официанта.

    allergens сразу начинают резать выдачу (hard-фильтр в ml/recommend.py) —
    заметка не декоративная, следующая рекомендация будет уже другой.
    """
    with get_engine().begin() as conn:
        row = conn.execute(text(UPSERT_PREFS_SQL), {
            "cid": customer_id,
            "allergens": allergens or [],
            "dislikes": dislikes or [],
            "note": note,
        }).mappings().one()

    return {
        "customer_id": customer_id,
        "allergens": list(row["allergens"]),
        "dislikes": list(row["dislikes"]),
        "note": row["note"],
    }
