import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djshop.settings')
django_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import path  # noqa: E402

from shop.consumers import StockConsumer  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_app,
    'websocket': AuthMiddlewareStack(URLRouter([
        path('ws/stock/<int:product_id>/', StockConsumer.as_asgi()),
    ])),
})
