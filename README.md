# Boostaurant — прототип рекомендательной системы

ИИ-ассистент официанта: по номеру столика (или `customer_id`) отдаёт топ-5 персональных рекомендаций по меню.

Реальной интеграции с iiko нет — данные генерируются по архетипам клиентов. Telegram-бот делается поверх готового API.

## Запуск

```bash
docker compose up -d db                          # поднять postgres (схема применится сама)
docker compose up -d app                         # поднять API

docker compose exec app python -m data_gen.generate --reset   # наполнить БД
docker compose exec app python -m ml.train_svd                # обучить SVD
docker compose exec app python -m ml.train_kmeans             # обучить k-means
```

API на `http://localhost:8000`, интерактивная документация — `/docs`.

## Telegram-бот официанта

```bash
cp .env.example .env          # вписать BOT_TOKEN от @BotFather
docker compose --profile bot up -d bot
```

Три кнопки, каждая спрашивает номер столика:

| Кнопка | Что делает |
|---|---|
| 🍽 **Рекомендация** | топ-5 блюд + пометка, насколько они персональны |
| 👤 **Инфо о посетителе** | лояльность, визиты, средний чек, любимые блюда, **аллергии** |
| ✍️ **Оставить заметку** | быстрые теги (аллергия, не любит острое…) или свободный текст |

**Заметка не декоративная.** Быстрые теги совпадают с `menu_items.tags`, поэтому попадают в hard-фильтр: отметил «аллергия: молочное» — и в следующей же рекомендации этому гостю не будет ни чизкейка, ни капучино.

Бот вынесен в профиль `bot`, поэтому обычный `docker compose up` его не поднимает — API можно запускать без токена.

## Эндпоинты

```bash
curl "localhost:8000/recommendations?customer_id=<uuid>"
curl "localhost:8000/recommendations?table=5"     # заглушка iiko: столик → клиент
curl "localhost:8000/profile?table=5"             # инфо о госте
curl -X POST "localhost:8000/preferences?table=5" \
     -H 'Content-Type: application/json' \
     -d '{"allergens":["dairy"],"note":"просит соус отдельно"}'
curl -X POST "localhost:8000/retrain"             # переобучить модели
curl "localhost:8000/health"
```

Ответ содержит `source` — какой слой сработал:

```json
{
  "customer_id": "…", "table_number": 5, "source": "svd",
  "items": [{"id": "…", "name": "Рибай", "category": "стейки", "price": 3200, "score": 4.9}]
}
```

## Как это работает

Три слоя, по убыванию персонализации:

| Слой | Когда | Качество* |
|---|---|---|
| **SVD** (коллаборативная фильтрация) | клиент есть в обученной матрице | 88% |
| **k-means cold-start** | клиента нет в матрице, но есть хоть один заказ | 79% |
| **popularity** | о клиенте не известно ничего | — |

Затем hard-фильтр по аллергенам и антипредпочтениям (`customer_preferences`) и буст по вкусу гостя — чтобы аллергия не выбрасывала его в «хвост» ранжирования, где персонализации уже нет (иначе сладкоежке с аллергией на молочное предлагается пиво).

\* доля позиций топ-5, попавших в «свои» категории архетипа. Цель гранта — >80%.

**Клиент попадает в матрицу SVD только при переобучении.** До этого он обслуживается слоем cold-start, сколько бы заказов ни сделал. Поэтому `/retrain` — не украшение: это момент, когда гость переезжает на персонализацию.

## Структура

```
db/schema.sql          5 таблиц; order_items — центральная для ML
data_gen/archetypes.py 5 архетипов клиентов + меню (56 блюд)
data_gen/generate.py   генератор фейковых данных
ml/features.py         фичи клиента: средний чек + доли категорий
ml/train_svd.py        SVD → models/svd.pkl
ml/train_kmeans.py     k-means + топы кластеров → models/kmeans.pkl
ml/recommend.py        recommend() — склейка трёх слоёв + hard-фильтр
api/main.py            FastAPI
api/profile.py         профиль гостя + запись заметок официанта
api/iiko_stub.py       заглушка: table_number → customer_id
bot/main.py            Telegram-бот (aiogram 3.x)
bot/handlers.py        3 кнопки, FSM на номер столика
bot/keyboards.py       клавиатуры; теги заметок = menu_items.tags
bot/api_client.py      HTTP-клиент к FastAPI
```

Подробности решений и результаты проверок по каждому этапу — в [prototype_plan.md](prototype_plan.md).

## Деплой на VPS (Beget)

```bash
# 1. вписать IP сервера в deploy.sh (строка SERVER_IP)
# 2. убедиться, что в .env есть BOT_TOKEN
./deploy.sh --seed      # первый раз: + генерация данных и обучение моделей
./deploy.sh             # последующие: только выкатка кода
./deploy.sh --tunnel    # API сервера на твоём localhost:8000
./deploy.sh --logs      # логи
./deploy.sh --down      # погасить
```

Скрипт сам поставит Docker, если его нет. Модели (`.pkl`) не копируются — они обучаются на сервере из его же БД: SVD-матрица привязана к конкретным `customer_id`.

### Публичный IP не нужен

Наружу не открыто ничего — ни Postgres, ни API. И это не ограничение, а следствие того, как всё устроено:

- **бот** работает через long polling: сам стучится к `api.telegram.org`, входящих соединений не принимает (вебхуки выключены). Ему нужен только исходящий интернет;
- **API** вызывает только бот, изнутри docker-сети, по `http://app:8000`.

Чтобы дёргать `/docs` и `/retrain` с локали, есть ssh-туннель:

```bash
./deploy.sh --tunnel     # → http://localhost:8000/docs
```

Так безопаснее открытого порта: у API нет ни авторизации, ни rate-limit, и торчащий наружу 8000 означал бы, что `POST /retrain` может дёрнуть любой, кто просканит порт.

**Характеристики сервера.** Приложение лёгкое: в работе `app` ест 140 МБ, пик при `/retrain` — 181 МБ, БД 11 МБ. Требования диктует **сборка образа**: `scikit-surprise` компилируется из Cython-исходников.

| | Минимум | Рекомендую |
|---|---|---|
| RAM | 2 ГБ | **4 ГБ** — на 2 ГБ gcc может словить OOM при сборке |
| CPU | 1 vCPU | **2 vCPU** — вдвое быстрее сборка (~5–10 мин) |
| Диск | 15 ГБ | **20 ГБ SSD** — образ 1.28 ГБ + слои Docker |
| ОС | | Ubuntu 22.04 / 24.04 |

Нужен **VPS** (root + Docker), не виртуальный хостинг. Публичный IP — **не нужен**.

## Локальный запуск без docker

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
docker compose up -d db
export DATABASE_URL="postgresql://boostaurant:boostaurant@localhost:5432/boostaurant"
.venv/bin/python -m data_gen.generate --reset
.venv/bin/python -m ml.train_svd && .venv/bin/python -m ml.train_kmeans
.venv/bin/uvicorn api.main:app --reload
```

> `numpy` и `scipy` запинены: `scikit-surprise` 1.1.4 собран под numpy C-API 1.x и с numpy 2.x падает на импорте.
