"""
WSGI config for cloud_approval project.
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cloud_approval.settings')

# Initialize Django app first
application = get_wsgi_application()

# Auto-run migrations and seed users on startup
# This ensures DB tables exist even if the post-deploy hook failed
def _auto_setup():
    try:
        from django.core.management import call_command
        from django.db import connection

        # Check if our core table exists
        tables = connection.introspection.table_names()
        if 'approval_system_userprofile' not in tables:
            print("[WSGI] Tables missing — running migrate...")
            call_command('migrate', '--noinput', '--run-syncdb', verbosity=0)
            call_command('seed_users', verbosity=0)
            print("[WSGI] Setup complete.")
        else:
            print("[WSGI] Tables OK — skipping migration.")
    except Exception as e:
        print(f"[WSGI] Auto-setup warning: {e}")

_auto_setup()
