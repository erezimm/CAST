"""
WSGI config for erez_tom project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""
import os
from django.core.wsgi import get_wsgi_application
import sys

# Add the parent directory to Python path
sys.path.append('/home/erezz/make-tom/erez_tom')
# print("Hello")

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erez_tom.settings')

application = get_wsgi_application()
