#!/bin/bash
# Run Django migrations and collect static files after deployment
# Amazon Linux 2023 with Python 3.9 on Elastic Beanstalk

source /var/app/venv/staging-*/bin/activate
cd /var/app/current

echo "Running Django migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Post-deploy setup complete."
