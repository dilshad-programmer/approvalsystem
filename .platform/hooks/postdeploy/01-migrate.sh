#!/bin/bash
# 01-migrate.sh — Standardized EB Post-Deploy Hook
# Handles: Migrations, Static Collection, Seeding, and Database Permissions

# Activate the EB virtual environment
source /var/app/venv/*/bin/activate

# CD into the app directory
cd /var/app/current

echo "=== [1/4] Running Django migrations ==="
python manage.py migrate --noinput --run-syncdb

echo "=== [2/4] Collecting static files ==="
python manage.py collectstatic --noinput --clear

echo "=== [3/4] Seeding initial users ==="
python manage.py seed_users

echo "=== [4/4] Fixing SQLite database permissions ==="
# Fix the "readonly database" error by ensuring the webapp user owns 
# the database file and the directory it resides in.
if [ -f db.sqlite3 ]; then
    chown webapp:webapp db.sqlite3
    chmod 664 db.sqlite3
    chown webapp:webapp .
    chmod 775 .
    echo "Permissions updated for db.sqlite3 and project root."
else
    echo "Warning: db.sqlite3 not found in current directory."
fi

echo "=== Post-deploy logic complete ==="
