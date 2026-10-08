from rest_framework import serializers

from .models import Category, Order, OrderLine, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']


class ProductSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())

    class Meta:
        model = Product
        fields = ['id', 'name', 'sku', 'price', 'stock', 'status', 'category', 'created_at']
        read_only_fields = ['created_at']


class DiscountSerializer(serializers.Serializer):
    percent = serializers.IntegerField(min_value=1, max_value=90)
    reason = serializers.CharField(max_length=80, required=False)


class OrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLine
        fields = ['product', 'quantity']


class OrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True)

    class Meta:
        model = Order
        fields = ['id', 'email', 'note', 'lines']

    def create(self, validated_data):
        lines = validated_data.pop('lines')
        order = Order.objects.create(**validated_data)
        for line in lines:
            OrderLine.objects.create(order=order, **line)
        return order
