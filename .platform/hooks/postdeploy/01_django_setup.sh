#!/bin/bash
# postdeploy/01_django_setup.sh
# Runs AFTER the new app version is live.
# Seeds initial users. Uses || true so a seed failure never kills the deploy.

# Note: NO set -e here — seed_users failing must not roll back the deployment.

# Activate the Python virtualenv (glob handles version-specific path)
source /var/app/venv/*/bin/activate 2>/dev/null || true

export DJANGO_SETTINGS_MODULE=cloud_approval.settings
export ENV=production

cd /var/app/current

echo "=== Seeding initial users (shawn, ajay, pravin) ==="
python manage.py seed_users || echo "[WARN] seed_users failed or already seeded — continuing."

echo "=== Post-deploy complete ==="
