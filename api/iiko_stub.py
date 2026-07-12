"""Заглушка iiko: номер столика → customer_id.

В проде это цепочка из трёх запросов в iiko (Поток A, data_model_plan.md):

    table_number
      → /reserve/available_restaurant_sections   (number → table_id)
      → /order/by_table (table_id)               (активный заказ → phone/customer.id)
      → /loyalty/iiko/customer/info (phone)      (→ customerId)
    ...если активного заказа нет:
      → /reserve/restaurant_sections_workload    (бронь → phone)

Реализовывать это в прототипе не нужно: данные фейковые, и цепочка сводится к
"кто сейчас сидит за столиком". Имитируем самым свежим заказом за этим столиком
— он и есть аналог "активного заказа" из /order/by_table.

Важно, что наружу заглушка отдаёт ровно то же, что отдаст боевая интеграция —
customer_id. Поэтому при переходе на реальную iiko меняется только этот модуль,
а api/main.py и ml/recommend.py не трогаются вовсе.
"""

from sqlalchemy import text

from db.connection import get_engine

# Аналог /order/by_table: кто сидит за столиком прямо сейчас.
# DISTINCT ON + ORDER BY DESC — самый свежий заказ за этим столиком.
ACTIVE_ORDER_SQL = """
    SELECT DISTINCT ON (o.table_number)
           o.customer_id::text AS customer_id
    FROM orders o
    WHERE o.table_number = :table_number
    ORDER BY o.table_number, o.created_at DESC
"""


def customer_id_by_table(table_number: int) -> str | None:
    """Клиент за столиком. None — столик пуст (в проде: нет ни заказа, ни брони)."""
    with get_engine().connect() as conn:
        return conn.execute(
            text(ACTIVE_ORDER_SQL), {"table_number": table_number}
        ).scalar()
