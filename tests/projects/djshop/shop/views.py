import json

from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.generic.edit import FormView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import FeedbackForm
from .models import Category, Order, Product
from .serializers import CategorySerializer, DiscountSerializer, OrderSerializer, ProductSerializer


class ProductViewSet(viewsets.ModelViewSet):
    """Products in the catalogue."""
    serializer_class = ProductSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'category']
    search_fields = ['name', 'sku']
    ordering_fields = ['price', 'created_at']

    def get_queryset(self):
        qs = Product.objects.all()
        if self.request.query_params.get('in_stock') == 'true':
            qs = qs.filter(stock__gt=0)
        return qs

    @action(detail=True, methods=['post'])
    def discount(self, request, pk=None):
        """Apply a percentage discount to the product's price."""
        product = self.get_object()
        serializer = DiscountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product.price = product.price * (100 - serializer.validated_data['percent']) / 100
        product.save()
        return Response(ProductSerializer(product).data)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]


class OrderView(APIView):
    """Orders of the signed-in customer."""

    def get(self, request):
        return Response(OrderSerializer(Order.objects.all(), many=True).data)

    def post(self, request):
        serializer = OrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def contact(request):
    """Send a message to the shop."""
    email = request.data.get('email')
    message = request.data['message']
    priority = int(request.data.get('priority', 1))
    if not email or '@' not in email:
        return Response({'error': 'email is required'}, status=400)
    return Response({'received': True, 'priority': priority, 'length': len(message)})


@api_view(['GET'])
def report(request, year):
    month = request.query_params.get('month')
    return Response({'year': year, 'month': month})


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def legacy(request, code):
    return Response({'code': code})


def health(request):
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@require_http_methods(['POST'])
def payment_webhook(request):
    data = json.loads(request.body)
    event = data['event']
    amount = data.get('amount', 0)
    return JsonResponse({'event': event, 'amount': amount})


class FeedbackView(FormView):
    form_class = FeedbackForm
    template_name = 'feedback.html'
    success_url = '/health/'

    def get(self, request, *args, **kwargs):
        return HttpResponse(f'<form method="post"><input name="csrfmiddlewaretoken" value="{get_token(request)}">')

    def form_invalid(self, form):
        return JsonResponse(form.errors, status=400)


@csrf_exempt
def newsletter(request):
    if request.method == 'POST':
        email = request.POST['email']
        return JsonResponse({'subscribed': email})
    return JsonResponse({'subscribers': 0})
