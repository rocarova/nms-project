"""
ASGI config for nms project.

Serves normal HTTP through Django and WebSockets (the SSH console) through Channels.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nms.settings')

# Initialize Django before importing anything that touches models
django_asgi_app = get_asgi_application()

if os.environ.get('NMS_SERVE_STATIC') == '1':
    # Set by `manage.py serve_https`, which runs without nginx in front to serve CSS/JS
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
    django_asgi_app = ASGIStaticFilesHandler(django_asgi_app)

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import path  # noqa: E402

from inventory.consumers import SSHConsumer  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(URLRouter([
        path('ws/ssh/<int:device_id>/', SSHConsumer.as_asgi()),
    ])),
})
