#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py ensure_admin
python manage.py load_icd10 --if-empty
python manage.py load_icd9 --if-empty

exec "$@"
