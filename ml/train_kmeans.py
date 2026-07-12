"""Обучение k-means для cold-start → ml/models/kmeans.pkl.

Зачем: у нового гостя нет истории, SVD ему бесполезен (в матрице
взаимодействий его нет). Но даже по одному заказу можно посчитать вектор фич,
определить кластер и отдать то, что любят похожие на него люди.

k-means центры НЕ задаются руками — он находит их сам (unsupervised).
customers.archetype в обучение не идёт: это была бы утечка. Архетип
используется ТОЛЬКО после обучения, чтобы проверить, восстановил ли алгоритм
зашитые сегменты по одному лишь поведению. Если да — значит в проде он найдёт
настоящие сегменты, которых мы не закладывали.

StandardScaler обязателен и сохраняется вместе с моделью: monetary измеряется
тысячами, доли категорий — в [0,1]. Без масштабирования евклидово расстояние
считалось бы почти целиком по деньгам, и кластеры вышли бы "богатые/бедные"
вместо "мясоеды/сладкоежки". На инференсе новый клиент должен масштабироваться
ТЕМИ ЖЕ mean/std, иначе попадёт не в тот кластер.

Запуск:
    python -m ml.train_kmeans
"""

import argparse
import pickle
from pathlib import Path

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from db.connection import get_engine
from ml.features import FEATURE_COLUMNS, all_customer_features

MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "kmeans.pkl"

N_CLUSTERS = 5  # столько же, сколько архетипов — чтобы проверка была осмысленной

# Топ блюд кластера считаем заранее, на обучении: на инференсе нужен быстрый ответ.
CLUSTER_TOP_SQL = """
    SELECT o.customer_id::text  AS customer_id,
           m.id::text           AS menu_item_id,
           m.name               AS name,
           m.category           AS category,
           m.price              AS price,
           SUM(oi.quantity)     AS qty
    FROM order_items oi
    JOIN orders     o ON o.id = oi.order_id
    JOIN menu_items m ON m.id = oi.menu_item_id
    WHERE m.is_active
    GROUP BY o.customer_id, m.id, m.name, m.category, m.price
"""

# Храним ранжирование ВСЕГО меню кластера, а не первые 20. Иначе при жёсткой
# аллергии hard-фильтр выбивает весь короткий список, и recommend() вынужден
# добивать глобальным popularity — то есть терять персонализацию там, где ниже
# по списку кластера ещё лежали безопасные и подходящие гостю блюда.
TOP_PER_CLUSTER = 200


def cluster_top_items(labels: pd.Series, n: int = TOP_PER_CLUSTER) -> dict[int, list[dict]]:
    """Для каждого кластера — самые заказываемые блюда его участников."""
    with get_engine().connect() as conn:
        df = pd.read_sql(text(CLUSTER_TOP_SQL), conn)

    df["cluster"] = df["customer_id"].map(labels)
    df = df.dropna(subset=["cluster"])

    tops: dict[int, list[dict]] = {}
    for cluster, grp in df.groupby("cluster"):
        agg = (
            grp.groupby(["menu_item_id", "name", "category", "price"], as_index=False)["qty"]
            .sum()
            .sort_values("qty", ascending=False)
            .head(n)
        )
        tops[int(cluster)] = [
            {
                "id": r.menu_item_id,
                "name": r.name,
                "category": r.category,
                "price": float(r.price),
                "score": float(r.qty),
            }
            for r in agg.itertuples()
        ]
    return tops


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение k-means (cold-start) для Boostaurant")
    parser.add_argument("--clusters", type=int, default=N_CLUSTERS)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    X = all_customer_features()
    print(f"Клиентов: {len(X)}, фич: {X.shape[1]} ({', '.join(FEATURE_COLUMNS[:3])}, + доли категорий)")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X.values)

    kmeans = KMeans(n_clusters=args.clusters, n_init=10, random_state=args.seed)
    labels = pd.Series(kmeans.fit_predict(X_scaled), index=X.index)

    tops = cluster_top_items(labels)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as f:
        # Скейлер, модель и топы кластеров — единый артефакт. Порознь бесполезны:
        # номера кластеров после переобучения меняются (k-means не гарантирует
        # порядок), поэтому топы нельзя хранить отдельно от модели, их породившей.
        pickle.dump(
            {
                "scaler": scaler,
                "kmeans": kmeans,
                "cluster_top_items": tops,
                "feature_columns": FEATURE_COLUMNS,
            },
            f,
        )

    print(f"\nМодель сохранена: {MODEL_PATH}")

    # --- валидация: совпали ли кластеры с зашитыми архетипами ---
    # Только для сгенерированных данных. У реального клиента archetype = NULL
    # (это служебное поле генератора), и на боевых данных валидация просто
    # пропускается. Она НЕ должна ронять переобучение: модель уже сохранена
    # выше, а диагностика — не повод отдать 500 в /retrain.
    with get_engine().connect() as conn:
        arch = pd.read_sql(
            text("SELECT id::text AS customer_id, archetype FROM customers "
                 "WHERE archetype IS NOT NULL"),
            conn,
        ).set_index("customer_id")["archetype"]

    labeled = labels.index.intersection(arch.index)
    if labeled.empty:
        print("(валидация пропущена: ни у кого нет archetype — это боевые данные)")
        return

    truth = arch.loc[labeled]
    pred = labels.loc[labeled]

    # Матрица кластер × архетип: видно, лёг ли каждый кластер на свой архетип.
    crosstab = pd.crosstab(pred, truth)
    # Purity: доля клиентов, попавших в доминирующий для своего кластера архетип.
    purity = crosstab.max(axis=1).sum() / crosstab.values.sum()

    print(f"\nКластер × архетип (по {len(labeled)} размеченным клиентам):")
    print(crosstab.to_string())
    print(f"\nARI    = {adjusted_rand_score(truth, pred):.3f}   "
          "(1.0 — идеальное совпадение, 0.0 — случайное)")
    print(f"Purity = {purity:.1%}")


if __name__ == "__main__":
    main()
