# План: Модель данных для прототипа Boostaurant

## Контекст

Прототип системы персонализированных рекомендаций для официантов. Алгоритм: коллаборативная фильтрация (item-based) + K-means кластеризация клиентов. Реальной интеграции с API не будет — данные генерируем, но схема и логика приближены к реальности.

Источники данных:
- **iiko** — CRM + система бронирования: история заказов, профиль клиента, программа лояльности, меню, бронирование столиков
- **Меню ресторана** — справочник блюд (берём напрямую из iiko)

---

## Подзадачи

### 1. Исследование API iiko ✅
Источник: `IIKO_API.json` (OpenAPI спецификация, 200 эндпоинтов).

#### Клиент — `POST /api/1/loyalty/iiko/customer/info`
Идентификация по: `phone` / `cardTrack` / `cardNumber` / `email` / `id`

Доступные поля ответа:
| Поле | Тип | Нужно для модели |
|------|-----|-----------------|
| `id` | uuid | ✅ PK |
| `phone` | string | ✅ ключ идентификации гостя |
| `name`, `surname` | string | нет |
| `birthday` | string (nullable) | опционально |
| `sex` | enum (0/1/2) | опционально |
| `email` | string | нет |
| `categories` | array | ✅ loyalty tier/сегмент |
| `walletBalances` | array | нет (для прототипа) |
| `cards` | array | нет |
| `firstOrderDate` | string (nullable) | ✅ давность клиента |
| `lastProcessedOrderDate` | string (nullable) | ✅ активность |
| `whenRegistered` | string | нет |

#### История заказов — двухшаговый процесс ⚠️
Прямого endpoint`а "все заказы клиента" нет. Нужно:

**Шаг 1** — `POST /api/1/loyalty/iiko/customer/transactions/by_date`
- Запрос: `customerId`, `dateFrom`, `dateTo`, `pageNumber`, `pageSize`, `organizationId`
- Ответ (на уровне транзакции):
  | Поле | Описание |
  |------|----------|
  | `posOrderId` | ID заказа для шага 2 |
  | `orderSum` | сумма заказа |
  | `whenCreatedOrder` | дата заказа |
  | `orderNumber` | номер заказа |
  | `balanceBefore/After` | баллы до/после |
  | `programId` | программа лояльности |

**Шаг 2** — `POST /api/1/order/by_id`
- Запрос: `posOrderIds` (из шага 1), `organizationIds`
- Ответ: полный заказ с позициями (`items`)

Поля позиции заказа (`ProductOrderItem`):
| Поле | Тип | Нужно |
|------|-----|-------|
| `product` | ref → Product | ✅ product_id |
| `amount` | number | ✅ количество |
| `price` | number | ✅ цена на момент заказа |
| `resultSum` | number | нет |
| `positionId` | string | нет |

#### Меню — `POST /api/1/nomenclature`
Запрос: `organizationId` (+ опционально `startRevision` для инкрементальных обновлений)

Поля блюда (`ProductInfo`):
| Поле | Нужно для модели |
|------|-----------------|
| `id` | ✅ PK |
| `name` | ✅ |
| `code` | ✅ SKU |
| `groupId` | ✅ категория (группа меню) |
| `productCategoryId` | ✅ подкатегория |
| `sizePrices` → `price` | ✅ текущая цена |
| `tags` | ✅ array[string] — вегетарианское, острое и т.д. |
| `energyAmount` | опционально (КБЖУ) |
| `proteinsAmount`, `fatAmount`, `carbohydratesAmount` | опционально |
| `imageLinks` | нет |
| `type` | ✅ dish / good / modifier |
| `isDeleted` | ✅ фильтрация |

#### Webhooks
В спецификации есть `x-webhooks` (TableOrderUpdate, TableOrderError) — то есть real-time уведомления о заказах поддерживаются. Для прототипа не нужно, но для продакшна можно использовать вместо polling.

#### Вывод по iiko
Все необходимые данные доступны. Особенность: история заказов требует 2 API-запроса на клиента. В синхронизаторе это нужно учесть (batching по `posOrderIds`). Меню поддерживает инкрементальные обновления через `startRevision`.

### 2. Идентификация клиента по столику — iiko Reserve API ✅

#### Справочник столиков — `POST /api/1/reserve/available_restaurant_sections`

Возвращает секции зала и их столики.

Схема `Table`:
| Поле | Тип | Нужно |
|------|-----|-------|
| `id` | string (uuid) | ✅ внутренний ключ |
| `number` | integer | ✅ номер столика (видит официант) |
| `name` | string | ✅ название |
| `seatingCapacity` | integer | нет |

#### Активный заказ за столиком — `POST /api/1/order/by_table`

Запрос: `tableIds`, `organizationIds`, `statuses`, `dateFrom/To`

Схема `TableOrder` (ответ):
| Поле | Тип | Нужно |
|------|-----|-------|
| `tableIds` | array[string] | ✅ |
| `customer.id` | string (uuid) | ✅ ID клиента в iiko |
| `phone` | string | ✅ телефон гостя |
| `status` | enum | ✅ фильтр активных |
| `items` | array[OrderItem] | ✅ текущие позиции |
| `sum` | number | нет |
| `whenCreated` | string | нет |

#### Бронирование столика — `POST /api/1/reserve/restaurant_sections_workload`

Возвращает список броней для секций за период. Используется если заказ ещё не открыт, но клиент забронировал стол.

Схема `Reserve`:
| Поле | Тип | Нужно |
|------|-----|-------|
| `phone` | string | ✅ ключ идентификации |
| `customer.id` | string (uuid) | ✅ если уже есть в iiko |
| `customer.name` | string | ✅ имя для UI |
| `tableIds` | array[string] | ✅ |
| `status` | enum | ✅ фильтр |
| `estimatedStartTime` | string | нет |

`customer` тут — объект `RegularCustomer`: `id`, `name`, `surname`, `email`, `gender`, `birthdate`

#### Поток A — Real-time: запрос рекомендации (при обслуживании гостя)

```
Официант вводит номер столика (например: "5")
        ↓
iiko: /reserve/available_restaurant_sections → table_id по number
        ↓
iiko: /order/by_table (table_id) — есть активный заказ?
    ДА → TableOrder.phone или TableOrder.customer.id
    НЕТ → /reserve/restaurant_sections_workload → Reserve.phone
        ↓
iiko: /loyalty/iiko/customer/info (phone) → customerId
        ↓
Наша БД: есть такой customerId?
    ДА → ML-модель(customerId) → топ-N рекомендаций
    НЕТ → fallback: популярные блюда за последние 30 дней
```

#### Поток B — Батч-синк: обновление обучающих данных (по расписанию)

```
По расписанию (раз в сутки или неделю):
        ↓
iiko: /deliveries/by_revision (startRevision) → заказы + customer.id
        ↓ собираем уникальные customerId (новые + изменившиеся)
iiko: /loyalty/iiko/customer/info (customerId) → профиль, categories, loyalty
        ↓
iiko: /loyalty/iiko/customer/transactions/by_date (customerId, период) → posOrderIds
        ↓
iiko: /order/by_id (posOrderIds) → полные заказы с позициями
        ↓
Сохраняем в нашу БД (customers, orders, order_items)
        ↓
Обновляем ML-модель
```

> **Откуда берётся список `customerId`.** Прямого эндпоинта «все клиенты» в iiko Cloud API нет: `customer/info` и `transactions/*` требуют уже известный идентификатор (phone / card / email / id). Список гостей формируется как побочный продукт синхронизации заказов — `customer.id` извлекается из заказов, выгружаемых инкрементально через `/deliveries/by_revision` (хранится последний `revision`, тянутся только изменения). Таблица `customers` в нашей БД наполняется постепенно, прогон за прогоном.
>
> ⚠️ `deliveries/*` покрывает доставку/самовывоз. Полная историческая выгрузка **всех** продаж, включая зал (dine-in), в проде берётся из OLAP-отчётов iiko Back-office API (`/resto/api/v2/reports/olap`) — этого API нет в `IIKO_API.json` (это Cloud/Transport API). Для прототипа неважно: данные генерируются, клиенты создаются с uuid-ами.

> Строки iiko-запросов из потока B — задача синхронизатора, не рекомендательного сервиса. Описывается текстом в отчёте, код не реализуется.

#### Вывод по идентификации

- Вся идентификация — через **iiko**
- Ключ связи «столик → клиент»: `table_number` → `table_id` → `phone` → `customerId`
- В real-time делаем 2–3 быстрых запроса в iiko, затем сразу в нашу БД
- История заказов в real-time не нужна — она уже в БД после батч-синка

### 3. Определение минимального набора атрибутов
На основе п.1–2 фиксируем решение по каждому атрибуту:
- Доступен из API → включаем как есть
- Недоступен из API → генерируем в фейковых данных (с реалистичным распределением)
- Не нужен для ML-модели → не включаем в схему

Результат: таблица "атрибут → источник → нужен для модели → решение"

### 4. Схема БД (PostgreSQL)
Проектируем на основе п.3. Ориентировочные таблицы:

- `customers` — профиль клиента
- `orders` — история заказов (заголовок)
- `order_items` — позиции заказа (связь заказ ↔ блюдо)
- `menu_items` — справочник блюд с категориями и тегами
- `customer_preferences` — аллергии, антипредпочтения (опционально, из опросника)
