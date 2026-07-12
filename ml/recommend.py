"""Единая точка рекомендаций: SVD → k-means cold-start → popularity.

    recommend(customer_id) — то, что зовёт API (этап 6).

Порядок слоёв и условие переключения:

    клиент ЕСТЬ в матрице SVD?   → SVD          (лучшая персонализация)
    иначе есть хоть один заказ?  → cold-start   (кластер похожих гостей)
    иначе                        → popularity   (топ за 30 дней)
    → hard-фильтр по аллергенам (слой 2) → top-n

Условие для SVD — "клиент в обученной матрице", а НЕ "у клиента есть заказы в
БД", как планировалось изначально. Разница принципиальная: клиент попадает в
матрицу только при переобучении модели, поэтому вчерашний гость с десятью
визитами для SVD всё ещё не существует. Surprise на незнакомый ID молча отдаёт
одну и ту же константу (у нас 2.596) на все 56 блюд — сортировка такого списка
даёт произвольный порядок, то есть мусор под видом рекомендации.

Замер на клиентах, которых модели не видели (топ-5, попадание в архетип):

    визитов у новичка     SVD    cold-start
            1             40%       82%
            3             40%       92%
           10             25%      100%

40% у SVD — это шум сортировки константы, а не работа модели. Поэтому новичка
всегда забирает cold-start, сколько бы заказов он ни сделал: до переобучения
SVD ему нечего сказать.

Про exclude_seen. По умолчанию НЕ прячем уже заказанные блюда. Для официанта
"гость всегда берёт рибай" — валидная и полезная подсказка, а в ресторане
повторный заказ любимого блюда это норма, в отличие от кино или книг, где
рекомендовать просмотренное бессмысленно. Флаг оставлен на случай, когда нужны
именно новинки ("что ещё предложить").
"""

import pickle
from functools import lru_cache
from pathlib import Path

from sqlalchemy import text

from db.connection import get_engine
from ml.features import customer_features

MODEL_PATH = Path(__file__).parent / "models" / "svd.pkl"
KMEANS_PATH = Path(__file__).parent / "models" / "kmeans.pkl"

ACTIVE_ITEMS_SQL = """
    SELECT id::text AS id, name, category, price
    FROM menu_items
    WHERE is_active
"""

SEEN_ITEMS_SQL = """
    SELECT DISTINCT oi.menu_item_id::text AS id
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    WHERE o.customer_id = :customer_id
"""


@lru_cache(maxsize=1)
def load_model():
    """Модель читается с диска один раз — API не должен дёргать pickle на каждый запрос."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"{MODEL_PATH} не найден — запусти python -m ml.train_svd")
    with MODEL_PATH.open("rb") as f:
        return pickle.load(f)


def active_menu_items() -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(ACTIVE_ITEMS_SQL)).mappings()]


def seen_item_ids(customer_id: str) -> set[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(text(SEEN_ITEMS_SQL), {"customer_id": customer_id})
        return {r["id"] for r in rows.mappings()}


def has_history(customer_id: str) -> bool:
    return bool(seen_item_ids(customer_id))


def recommend_svd(customer_id: str, n: int = 5, exclude_seen: bool = False) -> list[dict]:
    """Топ-n блюд для клиента с историей заказов.

    SVD предсказывает ожидаемый count для каждой пары (клиент, блюдо) — включая
    те, что клиент не пробовал: латентные факторы переносят вкус похожих гостей.
    Сортируем меню по этому предсказанию.
    """
    model = load_model()
    items = active_menu_items()

    if exclude_seen:
        seen = seen_item_ids(customer_id)
        items = [it for it in items if it["id"] not in seen]

    scored = []
    for item in items:
        # Клиента нет в обучающей выборке → SVD бесполезен (для unbiased-модели
        # предсказания выродятся в константу). Такого клиента должен забрать
        # cold-start слой, поэтому сюда он попадать не должен: recommend() на
        # этапе 5 проверяет has_history() до вызова.
        est = model.predict(customer_id, item["id"]).est
        scored.append({**item, "score": round(est, 3)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:n]


@lru_cache(maxsize=1)
def load_kmeans() -> dict:
    """Артефакт k-means: scaler + модель + топы кластеров, единым куском.

    Порознь они бесполезны: номера кластеров после переобучения меняются
    (k-means не гарантирует порядок), поэтому топы валидны только для той
    модели, которая их породила.
    """
    if not KMEANS_PATH.exists():
        raise FileNotFoundError(f"{KMEANS_PATH} не найден — запусти python -m ml.train_kmeans")
    with KMEANS_PATH.open("rb") as f:
        return pickle.load(f)


def predict_cluster(customer_id: str) -> int | None:
    """Кластер клиента. None — если заказов нет вообще (→ popularity-fallback)."""
    art = load_kmeans()
    feats = customer_features(customer_id)
    if feats is None:
        return None

    # Порядок колонок — из артефакта: скейлер помнит позиции, а не имена.
    X = feats[art["feature_columns"]].values
    return int(art["kmeans"].predict(art["scaler"].transform(X))[0])


def recommend_coldstart(customer_id: str, n: int = 5) -> list[dict]:
    """Топ-n для клиента без достаточной истории: популярное в ЕГО кластере.

    Работает уже по одному визиту: даже единственный заказ даёт средний чек и
    доли категорий, а этого хватает, чтобы определить сегмент и отдать то, что
    любят похожие гости. Заказ салата и боула уведёт вектор в вегетарианский
    кластер — и мы порекомендуем не стейк.
    """
    cluster = predict_cluster(customer_id)
    if cluster is None:
        return []
    return load_kmeans()["cluster_top_items"].get(cluster, [])[:n]


# ---------------------------------------------------------------------------
# Слой 3: popularity — крайний fallback, истории нет вообще
# ---------------------------------------------------------------------------
POPULAR_SQL = """
    SELECT m.id::text      AS id,
           m.name          AS name,
           m.category      AS category,
           m.price         AS price,
           SUM(oi.quantity) AS score
    FROM order_items oi
    JOIN orders     o ON o.id = oi.order_id
    JOIN menu_items m ON m.id = oi.menu_item_id
    WHERE m.is_active
      AND o.created_at >= now() - make_interval(days => :days)
    GROUP BY m.id, m.name, m.category, m.price
    ORDER BY score DESC
    LIMIT :limit
"""

POPULARITY_WINDOW_DAYS = 30


def recommend_popular(n: int = 5, days: int = POPULARITY_WINDOW_DAYS) -> list[dict]:
    """Топ блюд за последние N дней. Для гостя, о котором мы не знаем ничего."""
    with get_engine().connect() as conn:
        rows = conn.execute(text(POPULAR_SQL), {"days": days, "limit": n}).mappings()
        return [dict(r) | {"price": float(r["price"]), "score": float(r["score"])} for r in rows]


# ---------------------------------------------------------------------------
# Слой 2: hard-фильтр (аллергены / антипредпочтения)
# ---------------------------------------------------------------------------
PREFERENCES_SQL = """
    SELECT allergens, dislikes
    FROM customer_preferences
    WHERE customer_id = :customer_id
"""

ITEM_TAGS_SQL = """
    SELECT id::text AS id, tags
    FROM menu_items
"""


def _forbidden_item_ids(customer_id: str) -> set[str]:
    """Блюда, которые клиенту нельзя: пересечение menu_items.tags с его аллергенами.

    Это именно HARD-фильтр, а не понижение веса: аллергия — вопрос безопасности,
    такое блюдо не должно всплыть ни при каком score. dislikes режем так же
    (гость не обрадуется, даже если модель уверена).
    """
    with get_engine().connect() as conn:
        prefs = conn.execute(text(PREFERENCES_SQL), {"customer_id": customer_id}).mappings().first()
        if not prefs:
            return set()

        banned = set(prefs["allergens"] or []) | set(prefs["dislikes"] or [])
        if not banned:
            return set()

        rows = conn.execute(text(ITEM_TAGS_SQL)).mappings()
        return {r["id"] for r in rows if banned & set(r["tags"] or [])}


# ---------------------------------------------------------------------------
# Единая точка входа
# ---------------------------------------------------------------------------
def is_known_to_svd(customer_id: str) -> bool:
    """Есть ли клиент в ОБУЧЕННОЙ матрице (а не просто в БД).

    Клиент попадает сюда только при переобучении. До него SVD выдаёт константу
    на все блюда — то есть бесполезен, сколько бы заказов гость ни сделал.
    """
    try:
        load_model().trainset.to_inner_uid(customer_id)
        return True
    except (ValueError, FileNotFoundError):
        return False


# Насколько сильно поднимать блюда из любимых категорий гостя. Подобрано так,
# чтобы двигать ТОЛЬКО невнятный хвост (score 1.8-2.4), не трогая уверенный топ.
TASTE_BOOST = 1.5


def _boost_by_taste(customer_id: str, items: list[dict]) -> list[dict]:
    """Поднять блюда из категорий, которые гость реально ест.

    Зачем. SVD уверенно ранжирует то, что гость БРАЛ, а на остальном меню его
    оценки сползают к шуму (у нас 1.8-2.4), где мясо и вино случайно оказываются
    выше кофе. Обычно это неважно — мы показываем топ-5, который модель знает
    твёрдо. Но если hard-фильтр выбил весь топ (сладкоежка с аллергией на
    молочное теряет 26 блюд из 56), мы оказываемся ровно в этом хвосте, и
    официант получает "к десерту предложите пиво": эспрессо и сорбет стояли на
    14-м и 20-м местах со score ниже курицы терияки — только потому, что гостья
    их никогда не заказывала.

    Лечим долями категорий из ml/features.py — тем же вектором, что кормит
    k-means. Прибавка пропорциональна доле категории в заказах гостя, поэтому
    твёрдый топ не сдвигается (там разрыв в score велик), а хвост
    переупорядочивается в сторону его вкуса.
    """
    feats = customer_features(customer_id)
    if feats is None or not items:
        return items

    row = feats.iloc[0]
    for it in items:
        share = float(row.get(f"cat_{it['category']}", 0.0))
        it["score"] = round(it["score"] + TASTE_BOOST * share, 3)

    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def recommend(customer_id: str, n: int = 5, apply_filter: bool = True) -> dict:
    """Топ-n блюд для клиента. Возвращает и сами позиции, и какой слой сработал.

    Слой в ответе — не отладка: официанту и менеджеру важно понимать, персональная
    это рекомендация или "просто популярное", а API отдаёт это в поле source.

    Фильтруем ПОЛНОЕ ранжирование слоя, а не первые n*4 позиций. Разница
    принципиальная, когда аллергия выбивает весь топ: сладкоежке с аллергией на
    молочное фильтр срезал все 5 десертов, и добивка популярным подсовывала ей
    пиво и курицу терияки — топ продаж мясоедов, к её вкусу отношения не имеющий.
    А ниже по ЕЁ же ранжированию спокойно лежали сорбет и эспрессо: безмолочные,
    но всё ещё её. Персонализацию терять незачем — надо просто идти глубже по
    списку, который слой уже отранжировал.

    Popularity остаётся, но как последний рубеж: только если безопасных блюд в
    персональном ранжировании физически не хватило (аллергия на пол-меню).
    """
    banned = _forbidden_item_ids(customer_id) if apply_filter else set()

    def keep(items: list[dict]) -> list[dict]:
        return [it for it in items if it["id"] not in banned]

    # Просим у слоя ВСЁ меню: обрезаем после фильтра, а не до.
    full = len(active_menu_items())

    if is_known_to_svd(customer_id):
        source, items = "svd", keep(recommend_svd(customer_id, n=full))
    elif has_history(customer_id):
        source, items = "coldstart", keep(recommend_coldstart(customer_id, n=full))
    else:
        source, items = "popularity", keep(recommend_popular(n=full))

    items = _boost_by_taste(customer_id, items)

    # Своих безопасных блюд не хватило — только теперь добиваем популярным.
    if len(items) < n:
        seen = {it["id"] for it in items}
        items += [it for it in keep(recommend_popular(n=full)) if it["id"] not in seen]

    return {"customer_id": customer_id, "source": source, "items": items[:n]}
