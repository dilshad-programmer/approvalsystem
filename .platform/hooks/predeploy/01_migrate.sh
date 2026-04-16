#!/bin/bash
# predeploy/01_migrate.sh
# Runs BEFORE the new app version goes live.
# Handles schema migrations and static file collection.

set -e

# Activate the Python virtualenv (glob handles version-specific path)
source /var/app/venv/*/bin/activate

# Tell Django we are in production — skip load_dotenv, use IAM role
export DJANGO_SETTINGS_MODULE=cloud_approval.settings
export ENV=production

cd /var/app/staging

echo "=== Running database migrations ==="
python manage.py migrate --noinput

echo "=== Collecting static files ==="
python manage.py collectstatic --noinput

echo "=== Pre-deploy complete ==="
