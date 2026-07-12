"""Генератор фейковых данных → PostgreSQL.

Порядок (важен из-за FK): menu_items → customers → orders → order_items.

Блюда в заказе сэмплируются из распределения архетипа клиента (archetypes.py),
а не равномерно — иначе в матрице взаимодействий не будет структуры и SVD
нечему учиться. NOISE_RATE подмешивает случайные блюда: без шума данные
идеально сепарабельны и прототип выглядит нечестно хорошо.

Запуск:
    docker compose exec app python -m data_gen.generate
    python -m data_gen.generate --customers 300 --seed 42 --reset
"""

import argparse
import random
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import text

from data_gen.archetypes import ARCHETYPES, MENU, MenuItem
from db.connection import get_engine

# Доля позиций, взятых равномерно по всему меню вместо распределения архетипа.
NOISE_RATE = 0.12

# Глубина истории заказов.
HISTORY_DAYS = 180

# Столики в зале — для заглушки iiko (table_number → customer_id) в API.
TABLE_COUNT = 20

# Аллергены для customer_preferences: пересекаются с menu_items.tags.
ALLERGEN_POOL = ["gluten", "dairy", "seafood", "eggs", "soy", "nuts"]
DISLIKE_POOL = ["острое", "грибы", "лук", "кинза", "оливки"]

LOYALTY_WEIGHTS = {"bronze": 0.45, "silver": 0.30, "gold": 0.20, "platinum": 0.05}


def weighted_choice(rnd: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rnd.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def insert_menu(conn, rnd: random.Random) -> dict[str, list[tuple[uuid.UUID, MenuItem]]]:
    """Пишет меню, возвращает {category: [(id, MenuItem), ...]} для сэмплирования."""
    by_category: dict[str, list[tuple[uuid.UUID, MenuItem]]] = {}
    rows = []
    for item in MENU:
        item_id = uuid.uuid4()
        by_category.setdefault(item.category, []).append((item_id, item))
        rows.append(
            {
                "id": item_id,
                "name": item.name,
                "category": item.category,
                "price": item.price,
                "tags": list(item.tags),
                "is_active": True,
            }
        )
    conn.execute(
        text(
            "INSERT INTO menu_items (id, name, category, price, tags, is_active) "
            "VALUES (:id, :name, :category, :price, :tags, :is_active)"
        ),
        rows,
    )
    return by_category


def make_customer(rnd: random.Random, today: date) -> dict:
    archetype = rnd.choice(ARCHETYPES)
    # first_order_date — давность клиента; заказы генерируем начиная с неё.
    first_order = today - timedelta(days=rnd.randint(30, HISTORY_DAYS))
    tier_pool = {t: LOYALTY_WEIGHTS[t] for t in archetype.loyalty_tiers}
    return {
        "id": uuid.uuid4(),
        "phone": f"+79{rnd.randint(100000000, 999999999)}",
        "gender": rnd.choice([0, 1, 2]),
        "birthday": date(rnd.randint(1965, 2005), rnd.randint(1, 12), rnd.randint(1, 28)),
        "loyalty_tier": weighted_choice(rnd, tier_pool),
        "first_order_date": first_order,
        "registered_at": datetime.combine(first_order, datetime.min.time()),
        "archetype": archetype.code,
        # не в БД — нужно генератору дальше
        "_archetype": archetype,
    }


def sample_order_items(rnd, archetype, by_category, menu_flat) -> list[tuple[uuid.UUID, MenuItem]]:
    """Сэмплирует позиции одного заказа из распределения архетипа."""
    n_items = rnd.randint(*archetype.items_per_order)
    chosen: list[tuple[uuid.UUID, MenuItem]] = []
    seen: set[uuid.UUID] = set()

    for _ in range(n_items):
        if rnd.random() < NOISE_RATE:
            # шум: любое блюдо из меню
            item_id, item = rnd.choice(menu_flat)
        else:
            category = weighted_choice(rnd, archetype.category_weights)
            pool = by_category[category]
            # внутри категории — по item_weight ("любимые" блюда архетипа)
            item_id, item = rnd.choices(pool, weights=[m.item_weight for _, m in pool], k=1)[0]

        # одна и та же позиция дважды в заказе — это quantity, а не вторая строка
        if item_id in seen:
            continue
        seen.add(item_id)
        chosen.append((item_id, item))

    return chosen


def generate_orders(rnd, customers, by_category, today):
    """Строит orders/order_items для всех клиентов."""
    menu_flat = [pair for pool in by_category.values() for pair in pool]
    order_rows, item_rows = [], []

    for cust in customers:
        archetype = cust["_archetype"]
        n_visits = rnd.randint(*archetype.visits_range)
        first_order = cust["first_order_date"]
        span_days = max((today - first_order).days, 1)

        for _ in range(n_visits):
            visit_day = first_order + timedelta(days=rnd.randint(0, span_days))
            # weekday_bias: бизнес-ланч почти не ходит в выходные
            if visit_day.weekday() >= 5 and rnd.random() < archetype.weekday_bias:
                visit_day -= timedelta(days=rnd.randint(1, 2))
            created_at = datetime.combine(visit_day, datetime.min.time()) + timedelta(
                hours=rnd.randint(11, 22), minutes=rnd.randint(0, 59)
            )

            items = sample_order_items(rnd, archetype, by_category, menu_flat)
            if not items:
                continue

            order_id = uuid.uuid4()
            total = 0.0
            for item_id, item in items:
                quantity = 1 if rnd.random() < 0.85 else 2
                total += item.price * quantity
                item_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "order_id": order_id,
                        "menu_item_id": item_id,
                        "quantity": quantity,
                        "price": item.price,
                    }
                )

            order_rows.append(
                {
                    "id": order_id,
                    "customer_id": cust["id"],
                    "created_at": created_at,
                    "total_sum": round(total, 2),
                    "table_number": rnd.randint(1, TABLE_COUNT),
                }
            )

    return order_rows, item_rows


def make_preferences(rnd, customers):
    """Аллергены/антипредпочтения примерно у четверти клиентов (hard-фильтр, слой 2)."""
    rows = []
    for cust in customers:
        if rnd.random() > 0.25:
            continue
        rows.append(
            {
                "customer_id": cust["id"],
                "allergens": rnd.sample(ALLERGEN_POOL, rnd.randint(1, 2)),
                "dislikes": rnd.sample(DISLIKE_POOL, rnd.randint(0, 2)),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор фейковых данных Boostaurant")
    parser.add_argument("--customers", type=int, default=300, help="сколько клиентов (по умолчанию 300)")
    parser.add_argument("--seed", type=int, default=42, help="seed для воспроизводимости")
    parser.add_argument("--reset", action="store_true", help="очистить таблицы перед генерацией")
    args = parser.parse_args()

    rnd = random.Random(args.seed)
    today = date.today()
    engine = get_engine()

    with engine.begin() as conn:
        if args.reset:
            # TRUNCATE ... CASCADE снимает вопрос порядка удаления по FK.
            conn.execute(
                text(
                    "TRUNCATE order_items, orders, customer_preferences, customers, menu_items CASCADE"
                )
            )

        by_category = insert_menu(conn, rnd)

        customers = [make_customer(rnd, today) for _ in range(args.customers)]
        conn.execute(
            text(
                "INSERT INTO customers (id, phone, gender, birthday, loyalty_tier, "
                "first_order_date, registered_at, archetype) VALUES (:id, :phone, :gender, "
                ":birthday, :loyalty_tier, :first_order_date, :registered_at, :archetype)"
            ),
            [{k: v for k, v in c.items() if not k.startswith("_")} for c in customers],
        )

        order_rows, item_rows = generate_orders(rnd, customers, by_category, today)
        conn.execute(
            text(
                "INSERT INTO orders (id, customer_id, created_at, total_sum, table_number) "
                "VALUES (:id, :customer_id, :created_at, :total_sum, :table_number)"
            ),
            order_rows,
        )
        conn.execute(
            text(
                "INSERT INTO order_items (id, order_id, menu_item_id, quantity, price) "
                "VALUES (:id, :order_id, :menu_item_id, :quantity, :price)"
            ),
            item_rows,
        )

        pref_rows = make_preferences(rnd, customers)
        if pref_rows:
            conn.execute(
                text(
                    "INSERT INTO customer_preferences (customer_id, allergens, dislikes) "
                    "VALUES (:customer_id, :allergens, :dislikes)"
                ),
                pref_rows,
            )

    print(
        f"Готово: {len(MENU)} блюд, {len(customers)} клиентов, "
        f"{len(order_rows)} заказов, {len(item_rows)} позиций, "
        f"{len(pref_rows)} профилей предпочтений."
    )


if __name__ == "__main__":
    main()
