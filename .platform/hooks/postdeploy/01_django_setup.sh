#!/bin/bash
# Post-deploy hook for Amazon Linux 2023 with Python 3.9
source /var/app/venv/staging-*/bin/activate
cd /var/app/current

echo "Running Django migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Seeding initial users (shawn, ajay, pravin)..."
python manage.py seed_users

echo "Post-deploy setup complete."
