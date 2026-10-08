from django.contrib import admin
from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from shop import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'categories', views.CategoryViewSet, basename='category')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include(router.urls)),
    path('api/v1/auth/token/', TokenObtainPairView.as_view(), name='token'),
    path('api/v1/auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('api/v1/orders/', views.OrderView.as_view(), name='orders'),
    path('api/v1/contact/', views.contact, name='contact'),
    path('api/v1/reports/<int:year>/', views.report, name='report'),
    re_path(r'^api/v1/legacy/(?P<code>[A-Z]{3})/$', views.legacy, name='legacy'),
    path('health/', views.health, name='health'),
    path('webhooks/payment/', views.payment_webhook, name='webhook'),
    path('feedback/', views.FeedbackView.as_view(), name='feedback'),
    path('newsletter/', views.newsletter, name='newsletter'),
]
