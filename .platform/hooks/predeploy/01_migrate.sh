#!/bin/bash
# Activate virtual environment
source /var/app/venv/*/bin/activate

# CD into the application staging directory
cd /var/app/staging

# Export Django settings module
export DJANGO_SETTINGS_MODULE=cloud_approval.settings

# Run database migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput
