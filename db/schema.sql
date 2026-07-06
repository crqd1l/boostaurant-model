-- Boostaurant — схема БД прототипа рекомендательной системы.
-- Применяется автоматически при первом старте контейнера postgres
-- (смонтирована в /docker-entrypoint-initdb.d/).
--
-- Центральная таблица для ML — order_items: именно она даёт матрицу
-- взаимодействий (customer_id, menu_item_id, count) для SVD.

-- ---------------------------------------------------------------------------
-- Справочник блюд
-- Источник в проде: iiko POST /api/1/nomenclature (ProductInfo).
-- ---------------------------------------------------------------------------
CREATE TABLE menu_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL,
    category     TEXT        NOT NULL,          -- группа меню: "стейки", "салаты", "десерты"...
    price        NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    tags         TEXT[]      NOT NULL DEFAULT '{}',  -- "vegetarian", "spicy", "gluten"... для hard-фильтра
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE   -- iiko isDeleted → NOT is_active
);

-- ---------------------------------------------------------------------------
-- Клиенты
-- Источник в проде: iiko POST /api/1/loyalty/iiko/customer/info.
-- archetype — служебное поле генератора: зашитый архетип клиента.
-- Нужен для валидации k-means (совпал ли предсказанный кластер с архетипом).
-- В реальных данных этого поля нет.
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone             TEXT UNIQUE,               -- ключ идентификации гостя (iiko phone)
    gender            SMALLINT,                  -- iiko sex enum: 0/1/2, nullable
    birthday          DATE,                      -- nullable
    loyalty_tier      TEXT,                      -- iiko categories → сегмент лояльности
    first_order_date  DATE,                      -- давность клиента (iiko firstOrderDate)
    registered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    archetype         TEXT                       -- служебное: "meat_lover", "vegetarian"...
);

-- ---------------------------------------------------------------------------
-- Заказы (заголовок)
-- Источник в проде: iiko POST /api/1/order/by_id (полный заказ).
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id   UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),   -- дата визита; для RFM recency и popularity-30d
    total_sum     NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (total_sum >= 0),
    table_number  INTEGER                                -- номер столика (для заглушки iiko в API)
);

-- ---------------------------------------------------------------------------
-- Позиции заказа — ЦЕНТРАЛЬНАЯ ТАБЛИЦА ML.
-- Из неё строится матрица (customer_id, menu_item_id, count) для SVD.
-- Источник в проде: items внутри order/by_id (ProductOrderItem).
-- ---------------------------------------------------------------------------
CREATE TABLE order_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id      UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    menu_item_id  UUID NOT NULL REFERENCES menu_items(id) ON DELETE RESTRICT,
    quantity      INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),  -- iiko amount
    price         NUMERIC(10, 2) NOT NULL CHECK (price >= 0)        -- цена на момент заказа
);

-- ---------------------------------------------------------------------------
-- Предпочтения клиента (аллергены / антипредпочтения)
-- Для hard-фильтра (слой 2). В проде — из опросника, не из iiko.
-- Опционально в v1: таблица может быть пустой.
-- ---------------------------------------------------------------------------
CREATE TABLE customer_preferences (
    customer_id   UUID PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    allergens     TEXT[] NOT NULL DEFAULT '{}',   -- пересекается с menu_items.tags
    dislikes      TEXT[] NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- Индексы под горячие запросы ML/инференса.
-- ---------------------------------------------------------------------------
-- SVD: сборка матрицы взаимодействий (GROUP BY customer_id, menu_item_id).
CREATE INDEX idx_order_items_menu_item ON order_items (menu_item_id);
CREATE INDEX idx_order_items_order     ON order_items (order_id);
-- RFM / popularity-30d: заказы клиента и заказы за период.
CREATE INDEX idx_orders_customer       ON orders (customer_id);
CREATE INDEX idx_orders_created_at     ON orders (created_at);
-- Real-time заглушка iiko: table_number → customer_id.
CREATE INDEX idx_orders_table_number   ON orders (table_number);
