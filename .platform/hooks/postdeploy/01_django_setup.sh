#!/bin/bash
# Post-deploy hook for Amazon Linux 2023 with Python 3.9
# Note: We do NOT use 'set -e' so that a non-critical failure (like seed_users)
# does not abort the entire deployment and leave the app in a broken state.

source /var/app/venv/staging-LQM1lest/bin/activate 2>/dev/null || \
source /var/app/venv/staging-*/bin/activate

cd /var/app/current

echo "=== Running Django migrations ==="
python manage.py migrate --noinput --run-syncdb
if [ $? -ne 0 ]; then
    echo "ERROR: migrate failed — check model/db compatibility"
fi

echo "=== Collecting static files ==="
python manage.py collectstatic --noinput --clear
if [ $? -ne 0 ]; then
    echo "WARNING: collectstatic had issues"
fi

echo "=== Seeding initial users (shawn, ajay, pravin) ==="
python manage.py seed_users
if [ $? -ne 0 ]; then
    echo "WARNING: seed_users had issues (users may already exist)"
fi

echo "=== Post-deploy complete ==="
