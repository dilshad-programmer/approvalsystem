#!/bin/bash
source /var/app/venv/*/bin/activate
export DJANGO_SETTINGS_MODULE=cloud_approval.settings
cd /var/app/staging
python manage.py migrate
python manage.py collectstatic --noinput
