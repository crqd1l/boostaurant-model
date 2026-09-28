#!/bin/bash
#
# Выкладка сайта (лендинг + страница проекта) в Yandex Object Storage.
#
#   ./site/deploy_site.sh
#
# Бакет www.boostaurant.ru с хостингом статического сайта; https — сертификат
# Let's Encrypt из Certificate Manager. Нужен настроенный yc (yc init).
#
# Публикуются только html, css и img — черновики .md и этот скрипт в бакет не попадают.
# HTML и CSS грузятся с явным charset: без него кириллица в браузере ломается.

set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/share/yandex-cloud/bin:$PATH"

BUCKET=s3://www.boostaurant.ru

put() {  # put <файл> <content-type> [cache-control]
  yc storage s3 cp "$1" "$BUCKET/$1" --content-type "$2" \
    ${3:+--cache-control "$3"} --only-show-errors
}

echo "🚀 Сайт → $BUCKET"

for f in index.html project/index.html; do
  put "$f" "text/html; charset=utf-8" "no-cache"
done
put style.css "text/css; charset=utf-8" "no-cache"
put favicon.ico "image/x-icon" "public, max-age=604800"

# картинки: тип угадывается по расширению, кэш на неделю
yc storage s3 cp img "$BUCKET/img" --recursive \
  --cache-control "public, max-age=604800" --only-show-errors

echo "✅ Готово: https://www.boostaurant.ru/"
