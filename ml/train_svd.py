"""Обучение SVD (коллаборативная фильтрация) → ml/models/svd.pkl.

Вход — тройки (customer_id, menu_item_id, count) из order_items: сколько раз
клиент брал блюдо. Это implicit feedback, поданный как рейтинг: явных оценок
блюд гости не ставят, но повторный заказ — сильный сигнал симпатии.

Почему rating — НЕ сырой count (вопреки первоначальному решению в
prototype_plan.md). На сыром count-е SVD выучивает популярность вместо вкуса:
"Бизнес-ланч №1" лез в топ-5 всем архетипам подряд, включая гурмана. Причина в
том, что count мешает две разные величины — насколько клиент любит блюдо и как
часто он вообще ходит. Бизнес-ланч ходит 26 раз при меню из 4 комбо, поэтому
его счётчики вдвое выше всех прочих; гурман ходит 5 раз, и у него всё низкое.
Модель добросовестно училась предсказывать "сколько раз это блюдо заказывают
вообще" — то есть популярность.

Лечится нормализацией внутри клиента: рейтинг = доля блюда в заказах ЭТОГО
клиента, растянутая на шкалу 1..5. Вопрос меняется с "сколько раз в абсолюте"
на "какую часть своих заказов клиент отдал этому блюду" — а это уже вкус,
сравнимый между редким гурманом и частым бизнес-ланчем.

Запуск:
    python -m ml.train_svd
    python -m ml.train_svd --no-eval    # без hold-out оценки, быстрее
"""

import argparse
import pickle
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from surprise import SVD, Dataset, Reader
from surprise.accuracy import mae, rmse
from surprise.model_selection import train_test_split

from db.connection import get_engine

MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "svd.pkl"

# Матрица взаимодействий. Берём только активные блюда: рекомендовать снятое
# с меню бессмысленно, а в истории оно остаётся.
INTERACTIONS_SQL = """
    SELECT o.customer_id::text  AS customer_id,
           oi.menu_item_id::text AS menu_item_id,
           SUM(oi.quantity)      AS count
    FROM order_items oi
    JOIN orders     o ON o.id = oi.order_id
    JOIN menu_items m ON m.id = oi.menu_item_id
    WHERE m.is_active
    GROUP BY o.customer_id, oi.menu_item_id
"""


RATING_SCALE = (1.0, 5.0)

# biased=False — ключевое решение этапа, а не тюнинг ради метрики.
# Обычный SVD предсказывает: global_mean + bias_клиента + bias_блюда + факторы.
# Bias блюда — это ровно "насколько блюдо популярно вообще", один на всех
# клиентов. Замер на наших данных: std(item_bias)=0.29 против std(факторов)=0.22,
# то есть популярность весила БОЛЬШЕ персонального вкуса, и в топ-5 всем подряд
# лез "Бизнес-ланч №1" — самое заказываемое блюдо в базе.
# Выключение bias-ов заставляет модель объяснять рейтинг ТОЛЬКО латентными
# факторами вкуса. Попадание в архетип: 50% → 83%.
# Популярность при этом никуда не девается — она остаётся как отдельный слой
# fallback (этап 5), где она и уместна: для гостей вообще без истории.
BIASED = False

# Подобрано по целевой метрике (попадание топ-5 в категории архетипа), не по RMSE.
N_FACTORS = 100
N_EPOCHS = 250
LR_ALL = 0.01
REG_ALL = 0.02


def load_interactions() -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(INTERACTIONS_SQL), conn)


def add_rating(df: pd.DataFrame) -> pd.DataFrame:
    """count → rating: доля блюда в заказах клиента, растянутая на шкалу 1..5.

    share = count / max(count) внутри клиента. Любимое блюдо клиента всегда
    получает 5.0, независимо от того, взял он его 12 раз (бизнес-ланч) или
    2 раза (гурман). Именно это уравнивает частых и редких гостей и убирает
    популярность из сигнала.
    """
    lo, hi = RATING_SCALE
    peak = df.groupby("customer_id")["count"].transform("max")
    df["rating"] = lo + (hi - lo) * (df["count"] / peak)
    return df


def build_dataset(df: pd.DataFrame) -> Dataset:
    reader = Reader(rating_scale=RATING_SCALE)
    return Dataset.load_from_df(df[["customer_id", "menu_item_id", "rating"]], reader)


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение SVD для Boostaurant")
    parser.add_argument("--no-eval", action="store_true", help="пропустить hold-out оценку")
    parser.add_argument("--factors", type=int, default=N_FACTORS, help="число латентных факторов")
    parser.add_argument("--epochs", type=int, default=N_EPOCHS, help="число эпох SGD")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = load_interactions()
    if df.empty:
        raise SystemExit("order_items пуст — сначала прогони data_gen.generate")

    df = add_rating(df)

    print(
        f"Взаимодействий: {len(df)} "
        f"({df.customer_id.nunique()} клиентов × {df.menu_item_id.nunique()} блюд), "
        f"count ∈ [{df['count'].min()}, {df['count'].max()}] → "
        f"rating ∈ [{df['rating'].min():.2f}, {df['rating'].max():.2f}]"
    )

    data = build_dataset(df)

    def make_model() -> SVD:
        return SVD(
            n_factors=args.factors,
            n_epochs=args.epochs,
            lr_all=LR_ALL,
            reg_all=REG_ALL,
            biased=BIASED,
            random_state=args.seed,
        )

    if not args.no_eval:
        # Hold-out — санити-чек, что модель вообще учится, а НЕ критерий качества.
        # Настоящая проверка этапа — попадание топ-5 в категории архетипа: RMSE
        # тут обманчив, biased-модель давала RMSE лучше при вдвое худших
        # рекомендациях (популярное блюдо угадать легко, полезным оно от этого
        # не становится).
        trainset, testset = train_test_split(data, test_size=0.2, random_state=args.seed)
        model = make_model()
        model.fit(trainset)
        predictions = model.test(testset)
        print(f"\nHold-out (20%): RMSE={rmse(predictions, verbose=False):.4f}, "
              f"MAE={mae(predictions, verbose=False):.4f}")

    # Финальная модель учится на всех данных: hold-out был нужен только для оценки.
    model = make_model()
    model.fit(data.build_full_trainset())

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as f:
        pickle.dump(model, f)

    print(f"\nМодель сохранена: {MODEL_PATH}")


if __name__ == "__main__":
    main()
