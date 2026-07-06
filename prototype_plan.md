# План реализации прототипа рекомендательной системы

> Цель: рабочий прототип, который по `customer_id` (или номеру столика через заглушку) выдаёт топ-5 блюд. Реальной интеграции с iiko нет — данные генерируем. Telegram-бот делаем **потом**; сейчас только БД + ML + API.

Связанные документы: [data_model_plan.md](data_model_plan.md) (поля iiko), [architecture.md](architecture.md) (алгоритмы, потоки).

---

## Что важно понять до начала (снимает кашу)

**SVD и k-means едят разные данные — не путать.**

| Модель | Что на входе | Откуда | Зачем |
|--------|--------------|--------|-------|
| **SVD** | тройки `(customer_id, menu_item_id, count)` | `order_items` | основные рекомендации для клиентов с историей |
| **k-means** | фичи клиента (RFM + доли категорий) | агрегаты из `orders`/`order_items` | cold-start: новый клиент → его кластер → популярное в кластере |
| **Popularity** | суммы заказов за 30 дней | `order_items` | крайний fallback: истории нет вообще |

**Ключевой вывод:** 90% ценности — история заказов (`order_items`), а НЕ профиль клиента (пол/возраст/лояльность). Профиль нужен только для k-means cold-start.

**Решения (зафиксированы):**
- rating для SVD = **простой count** (сколько раз клиент заказывал блюдо). Взвешенный `log(1+count)×visits` из architecture.md — опциональное улучшение, не для базового прототипа.
- данные генерируем **по архетипам клиентов** (не случайно!), иначе SVD нечему учиться.

---

## Структура репозитория

```
boostaurant/
├── db/
│   ├── schema.sql          # DDL: 5 таблиц
│   └── connection.py       # подключение к postgres (psycopg2/sqlalchemy)
├── data_gen/
│   ├── archetypes.py       # определения 5 архетипов
│   └── generate.py         # генератор фейковых данных → пишет в БД
├── ml/
│   ├── train_svd.py        # обучение SVD → svd_model.pkl
│   ├── train_kmeans.py     # обучение k-means → kmeans_model.pkl
│   ├── recommend.py        # recommend(customer_id) → top-5 (с fallback)
│   └── models/             # сохранённые .pkl
├── api/
│   └── main.py             # FastAPI: GET /recommendations
├── requirements.txt
└── README.md
```

`requirements.txt`: `psycopg2-binary`, `sqlalchemy`, `pandas`, `scikit-surprise`, `scikit-learn`, `fastapi`, `uvicorn`, `numpy`.

---

## Этапы (по порядку)

### Этап 1 — SQL-схема (`db/schema.sql`)

5 таблиц. Минимум, достаточный для SVD + k-means + fallback:

- **`menu_items`** — `id, name, category, price, tags[], is_active`. Справочник блюд.
- **`customers`** — `id (uuid), phone, gender, birthday, loyalty_tier, first_order_date, registered_at, archetype`. Профиль. `archetype` — служебное поле, чтобы потом проверить, совпал ли кластер k-means с зашитым архетипом (валидация).
- **`orders`** — `id, customer_id (FK), created_at, total_sum, table_number`. Заголовок заказа.
- **`order_items`** — `id, order_id (FK), menu_item_id (FK), quantity, price`. Позиции. **Центральная таблица для ML.**
- **`customer_preferences`** — `customer_id (FK), allergens[], dislikes[]`. Для hard-фильтра (слой 2). Опционально в v1.

Проверка этапа: схема применяется в чистую БД без ошибок.

### Этап 2 — Архетипы и генератор (`data_gen/`)

**Архетипы (5 шт.)** — каждый задаёт вероятности заказа блюд по категориям:

| Архетип | Любит категории | Средний чек | Частота визитов |
|---------|-----------------|-------------|-----------------|
| Мясоед | стейки, бургеры, пиво | высокий | средняя |
| Вегетарианец | салаты, боулы, супы | средний | высокая |
| Сладкоежка | десерты, кофе, выпечка | низкий | высокая |
| Бизнес-ланч | супы, комбо, горячее | средний | очень высокая (будни) |
| Гурман | всё дорогое, вино | очень высокий | низкая |

**Генератор** делает:
1. Сначала меню (`menu_items`) — ~40-60 блюд по категориям, с ценами и тегами.
2. Клиентов (`customers`) — ~200-500, каждому случайно назначается архетип.
3. Для каждого клиента — N визитов (`orders`), в каждом визите блюда сэмплируются **из распределения его архетипа** (+ немного шума, чтобы не было идеально). Пишем `order_items`.

Проверка этапа: в БД есть данные; SQL-запрос «топ блюд по архетипу» показывает ожидаемый перекос (мясоеды берут стейки чаще).

### Этап 3 — SVD (`ml/train_svd.py` + часть `recommend.py`)

Обучение (по architecture.md, библиотека Surprise):
```python
df = SELECT customer_id, menu_item_id, COUNT(*) as cnt FROM order_items ... GROUP BY
reader = Reader(rating_scale=(1, df.cnt.max()))
data = Dataset.load_from_df(df[['customer_id','menu_item_id','cnt']], reader)
model = SVD(); model.fit(data.build_full_trainset())
pickle.dump(model, 'ml/models/svd.pkl')
```

Инференс — `recommend_svd(customer_id, n=5)`:
```python
scores = [(iid, model.predict(customer_id, iid).est) for iid in active_menu_items]
return sorted(scores, key=..., reverse=True)[:n]
```

Проверка этапа (**самая важная**): взять клиента-мясоеда → в топ-5 должны быть мясные блюда, а не десерты. Если рекомендации мусорные → проблема в данных (этап 2), а не в модели.

### Этап 4 — k-means cold-start (`ml/train_kmeans.py`)

1. Собрать фичи по каждому клиенту: RFM (recency = дней с последнего заказа, frequency = число визитов, monetary = средний чек) + доли категорий в заказах.
2. `StandardScaler` → `KMeans(n_clusters=5)`. Сохранить модель + скейлер.
3. Для каждого кластера заранее посчитать «топ блюд кластера».
4. `recommend_coldstart(customer_features)` → predict кластер → топ блюд кластера.

Проверка этапа: кластеры k-means примерно совпадают с зашитыми архетипами (для этого и хранили `customers.archetype`).

### Этап 5 — единая функция рекомендации (`ml/recommend.py`)

```
recommend(customer_id):
    если есть история в order_items → SVD (этап 3)
    иначе если есть профиль → k-means cold-start (этап 4)
    иначе → popularity fallback (топ-5 за 30 дней, SQL)
    → опционально: hard-фильтр по аллергенам (слой 2)
    → top-5
```

### Этап 6 — FastAPI (`api/main.py`)

```
GET /recommendations?customer_id=<uuid>   → {items: [...]}
GET /recommendations?table=<n>            → заглушка: table→customer_id (мок вместо iiko), потом SVD
POST /retrain                              → переобучить модели (для демо)
```

Заглушка iiko: словарь `{table_number: customer_id}` из наших фейковых данных — имитирует цепочку `table→phone→customer_id`, которую в проде даёт iiko (см. Поток A в architecture.md).

Проверка этапа: `curl "localhost:8000/recommendations?customer_id=..."` возвращает осмысленный топ-5.

---

## Порядок и зависимости

```
Этап 1 (схема)
   └→ Этап 2 (данные)          ← без данных ничего не обучить
        ├→ Этап 3 (SVD)        ← ядро, делаем первым из ML
        └→ Этап 4 (k-means)    ← можно после SVD
             └→ Этап 5 (recommend) ← склеивает SVD+kmeans+fallback
                  └→ Этап 6 (API)
```

Делаем строго по цепочке. После каждого этапа — его «проверка», не переходим дальше пока не зелёная.

## Что НЕ делаем в прототипе
- Реальные запросы в iiko (Поток B / синхронизатор) — только текст в отчёте.
- Telegram-бот — потом, поверх готового API.
- Взвешенный rating, инкрементальное переобучение, версионирование моделей — улучшения.
