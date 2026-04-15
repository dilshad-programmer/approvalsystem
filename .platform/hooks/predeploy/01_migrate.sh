#!/bin/bash

source "$PYTHONPATH/activate"
python manage.py migrate --noinput
python manage.py collectstatic --noinput
