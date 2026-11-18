
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.contrib.auth import get_user_model
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)

from accounts.authentication import JWTAuthentication
from accounts.permissions import CustomerOnly
from customers.models import (
    CustomerProfile,
    CustomerVehicle,
    Cart,
    Notification,
)
from customers.checkout_eligibility import customer_checkout_eligibility
from customers.serializers import CheckoutRequestSerializer
from products.models import Product
from shops.geofence import distance_between_coordinates
from shops.models import Shop
from shops.permissions import shop_can_accept_new_orders


from .constants import (
    get_high_value_threshold,
    order_requires_high_value_approval,
)
from .models import Order, OrderItem, OrderLiveLocation
from .shop_eligibility import (
    existing_order_can_continue,
    order_requires_super_admin_review,
)
from .serializers import (
    OrderLiveLocationResponseSerializer,
    OrderLiveLocationUpdateSerializer,
    OrderPaymentStatusSerializer,
    OrderSerializer,
    OrderTrackingSerializer,
)
from .payments import payment_projection


MINIMUM_ORDER_AMOUNT = Decimal("500.00")
APPLICATION_FEE = Decimal("50.00")
PICKUP_RADIUS_METERS = 100
LIVE_LOCATION_STALE_AFTER = timedelta(seconds=60)
LIVE_LOCATION_ORDER_STATUSES = {"confirmed", "preparing", "ready"}
User = get_user_model()


@extend_schema(request=CheckoutRequestSerializer, responses={201: OrderSerializer})
class OrderCreateView(generics.CreateAPIView):
    serializer_class = OrderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def create(self, request, *args, **kwargs):
        user = request.user

        profile, eligibility_failure = customer_checkout_eligibility(user)
        if eligibility_failure is not None:
            return Response(
                eligibility_failure.response_data(),
                status=eligibility_failure.status_code,
            )

        checkout_request = CheckoutRequestSerializer(data=request.data)
        if not checkout_request.is_valid():
            return Response(
                {
                    "detail": "Invalid checkout request.",
                    "errors": checkout_request.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        request_values = checkout_request.validated_data
        pickup_mode = request_values["pickup_mode"]

        vehicle = None

        with transaction.atomic():
            if pickup_mode == "vehicle":
                vehicle = CustomerVehicle.objects.select_for_update().filter(
                    id=request_values["vehicle_id"], customer=profile,
                ).first()
                if not vehicle:
                    return Response({"detail": "Vehicle not found."}, status=status.HTTP_400_BAD_REQUEST)

            cart, _ = Cart.objects.select_for_update().get_or_create(
                customer=profile
            )

            cart_items = list(
                cart.items.select_related(
                    "product",
                    "product__shop",
                # Cart rows are locked here; shop/product locks are acquired
                # below in the shared shop-then-product order. Locking joined
                # product rows here can invert that order against a Super Admin
                # high-value rejection and deadlock.
                ).select_for_update(of=("self",))
            )

            if not cart_items:
                return Response(
                    {"detail": "Cart is empty."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            first_shop = None
            order_items = []
            subtotal = Decimal("0.00")

            for item in cart_items:
                product = item.product
                shop = product.shop

                if first_shop is None:
                    first_shop = shop
                elif first_shop.id != shop.id:
                    return Response(
                        {
                            "detail": (
                                "Cart can contain products "
                                "from only one shop."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                item_subtotal = product.price * item.quantity
                subtotal += item_subtotal

                order_items.append(
                    {
                        "product": product,
                        "product_name": product.name,
                        "unit_price": product.price,
                        "quantity": item.quantity,
                        "subtotal": item_subtotal,
                    }
                )

            first_shop = Shop.objects.select_for_update().get(pk=first_shop.pk)
            if not shop_can_accept_new_orders(first_shop):
                return Response(
                    {
                        "detail": (
                            f"Shop '{first_shop.shop_name}' "
                            "is currently unavailable."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # After the shop lock, lock products deterministically and use the
            # fresh rows for stock checks and the eventual decrement.
            locked_products = {
                product.id: product
                for product in Product.objects.select_for_update().filter(
                    id__in=[item.product_id for item in cart_items]
                ).order_by("id")
            }
            subtotal = Decimal("0.00")
            for item in cart_items:
                product = locked_products[item.product_id]
                if not product.is_active:
                    return Response(
                        {"detail": f"Product '{product.name}' is no longer available."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if item.quantity > product.stock_quantity:
                    return Response(
                        {"detail": f"Only {product.stock_quantity} units of '{product.name}' are available."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                item_subtotal = product.price * item.quantity
                subtotal += item_subtotal
                for data in order_items:
                    if data["product"].id == product.id:
                        data["product"] = product
                        data["product_name"] = product.name
                        data["unit_price"] = product.price
                        data["subtotal"] = item_subtotal
                        break

            if subtotal < MINIMUM_ORDER_AMOUNT:
                return Response(
                    {
                        "detail": "Minimum product subtotal is ₹500 to place an order.",
                        "minimum_order_amount": MINIMUM_ORDER_AMOUNT,
                        "current_subtotal": subtotal,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if first_shop.latitude is None or first_shop.longitude is None:
                return Response(
                    {
                        "detail": (
                            "Shop location is unavailable; checkout "
                            "cannot be validated."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            distance_to_shop_m = distance_between_coordinates(
                request_values["latitude"],
                request_values["longitude"],
                first_shop.latitude,
                first_shop.longitude,
            )
            if distance_to_shop_m > PICKUP_RADIUS_METERS:
                return Response(
                    {
                        "detail": (
                            "You must be within 100 meters of the shop "
                            "to place an order."
                        ),
                        "pickup_radius_m": PICKUP_RADIUS_METERS,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            total_amount = subtotal + APPLICATION_FEE

            if total_amount >= get_high_value_threshold():
                order_status = "pending_super_admin"
            else:
                order_status = "pending"

            order = Order.objects.create(
                customer=user,
                shop=first_shop,
                status=order_status,
                payment_method="online",
                payment_status="pending",
                pickup_mode=pickup_mode,
                vehicle=vehicle,
                subtotal=subtotal,
                application_fee=APPLICATION_FEE,
                total_amount=total_amount,
            )

            for data in order_items:
                OrderItem.objects.create(
                    order=order,
                    product=data["product"],
                    product_name=data["product_name"],
                    unit_price=data["unit_price"],
                    quantity=data["quantity"],
                    subtotal=data["subtotal"],
                )

                product = data["product"]
                product.stock_quantity -= data["quantity"]
                product.save(
                    update_fields=["stock_quantity", "updated_at"]
                )

            cart.items.all().delete()

            if order_status == "pending_super_admin":
                message = (
                    f"Your order #{order.id} has been placed successfully. "
                    "Since the order total meets the high-value threshold, "
                    "it requires Super Admin handling."
                )
            else:
                message = (
                    f"Your order #{order.id} has been placed successfully "
                    "and will be handled by the shop."
                )

            Notification.objects.create(
                user=user,
                order=order,
                notification_type="order_placed",
                title="Order Placed",
                message=message,
            )

            if order_status == "pending_super_admin":
                for super_admin in User.objects.filter(
                    user_type="super_admin",
                    is_active=True,
                ):
                    Notification.objects.create(
                        user=super_admin,
                        order=order,
                        notification_type="general",
                        title="High-Value Order Pending",
                        message=(
                            f"Order #{order.id} requires Super Admin review."
                        ),
                    )

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )


class OrderHistoryView(generics.ListAPIView):
    serializer_class = OrderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return (
            Order.objects.filter(customer=self.request.user)
            .prefetch_related("items")
            .select_related("shop", "vehicle")
            .order_by("-created_at")
        )


class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return (
            Order.objects.filter(customer=self.request.user)
            .prefetch_related("items")
            .select_related("shop", "vehicle")
        )


class OrderCancelView(generics.GenericAPIView):
    serializer_class = OrderSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update(),
                id=kwargs["pk"],
                customer=request.user,
            )

            non_cancellable_statuses = [
                "preparing",
                "ready",
                "completed",
                "rejected",
                "cancelled",
            ]

            if order.status in non_cancellable_statuses:
                return Response(
                    {
                        "detail": (
                            f"Order cannot be cancelled because "
                            f"its status is '{order.status}'."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            order_items = list(
                OrderItem.objects.filter(order=order).order_by("product_id")
            )

            for item in order_items:
                product = Product.objects.select_for_update().get(
                    id=item.product_id
                )
                product.stock_quantity += item.quantity
                product.save(
                    update_fields=["stock_quantity", "updated_at"]
                )

            order.status = "cancelled"
            order.save(update_fields=["status", "updated_at"])

            Notification.objects.create(
                user=request.user,
                order=order,
                notification_type="order_update",
                title="Order Cancelled",
                message=(
                    f"Your order #{order.id} has been "
                    "cancelled successfully."
                ),
            )

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_200_OK,
        )


class PaymentStatusView(generics.RetrieveAPIView):
    serializer_class = OrderPaymentStatusSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def retrieve(self, request, *args, **kwargs):
        order = get_object_or_404(
            Order.objects.select_related("payment"),
            id=kwargs["pk"],
            customer=request.user,
        )

        return Response(
            {
                "order_id": order.id,
                "payment_method": order.payment_method,
                "payment_status": order.payment_status,
                "subtotal": order.subtotal,
                "application_fee": order.application_fee,
                "platform_fee": order.application_fee,
                "total_amount": order.total_amount,
                **payment_projection(order),
            },
            status=status.HTTP_200_OK,
        )


class OrderLiveLocationView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="pk",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="Order ID.",
            ),
        ],
        responses={
            200: OrderLiveLocationResponseSerializer,
            401: OpenApiResponse(description="Missing or invalid Django JWT."),
            403: OpenApiResponse(description="Authenticated role cannot view location."),
            404: OpenApiResponse(description="Order does not exist or is not accessible to this user."),
        },
        description=(
            "Returns the latest vehicle-pickup location for the order customer "
            "or owning Shop Admin. Coordinates are null when sharing is stopped, "
            "the location is stale, or the order is not eligible."
        ),
    )
    def get(self, request, pk):
        order = self._get_authorized_order(request, pk)
        if order is None:
            if request.user.user_type not in {"customer", "shop_admin"}:
                raise PermissionDenied(
                    "Only the order customer or owning Shop Admin can view location."
                )
            raise Http404

        location = OrderLiveLocation.objects.filter(order=order).first()
        active_order = self._is_location_eligible(order)
        stale = bool(
            location
            and location.is_sharing
            and timezone.now() - location.updated_at > LIVE_LOCATION_STALE_AFTER
        )
        has_coordinates = bool(
            location
            and location.latitude is not None
            and location.longitude is not None
        )
        is_sharing = bool(
            location
            and location.is_sharing
            and active_order
            and not stale
            and not order_requires_high_value_approval(order)
            and has_coordinates
        )
        if stale:
            OrderLiveLocation.objects.filter(
                pk=location.pk,
                is_sharing=True,
                updated_at__lte=timezone.now() - LIVE_LOCATION_STALE_AFTER,
            ).update(
                is_sharing=False,
                latitude=None,
                longitude=None,
            )
        return Response(
            {
                "order_id": order.id,
                "is_sharing": is_sharing,
                "latitude": (
                    float(location.latitude)
                    if is_sharing
                    else None
                ),
                "longitude": (
                    float(location.longitude)
                    if is_sharing
                    else None
                ),
                "updated_at": location.updated_at if location else None,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="pk",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="Order ID.",
            ),
        ],
        request=OrderLiveLocationUpdateSerializer,
        responses={
            200: OrderLiveLocationResponseSerializer,
            400: OpenApiResponse(
                description=(
                    "Invalid coordinates, unsupported pickup mode, or order "
                    "status outside confirmed/preparing/ready."
                )
            ),
            401: OpenApiResponse(description="Missing or invalid Django JWT."),
            403: OpenApiResponse(description="Only the owning Shop Admin can publish location."),
            404: OpenApiResponse(description="Order does not exist or is not owned by this Shop Admin."),
        },
        description=(
            "Only the owning Shop Admin can start/update sharing by sending "
            "is_sharing=true with latitude and longitude, or stop sharing by "
            "sending is_sharing=false without coordinates."
        ),
    )
    def post(self, request, pk):
        if request.user.user_type != "shop_admin":
            raise PermissionDenied("Shop Admin access required.")

        serializer = OrderLiveLocationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data

        with transaction.atomic():
            order = self._get_authorized_order(request, pk, for_update=True)
            if order is None:
                raise Http404
            if not self._is_location_eligible(order):
                return Response(
                    {
                        "detail": (
                            "Location sharing is available only for vehicle "
                            "pickup orders in confirmed, preparing, or ready status."
                        ),
                        "current_status": order.status,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if (
                values["is_sharing"]
                and order_requires_high_value_approval(order)
            ):
                return Response(
                    {
                        "detail": (
                            "Super Admin approval is required before "
                            "location sharing can start."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            if values["is_sharing"] and not existing_order_can_continue(order):
                return Response(
                    {
                        "detail": (
                            "This shop is restricted from publishing "
                            "location until Super Admin review."
                        ),
                        "review_required": order_requires_super_admin_review(
                            order
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            if not values["is_sharing"]:
                location = OrderLiveLocation.objects.filter(order=order).first()
                if location is None:
                    location = OrderLiveLocation.objects.create(
                        order=order,
                        publisher=request.user,
                        is_sharing=False,
                    )
                else:
                    location.publisher = request.user
                    location.is_sharing = False
                    location.latitude = None
                    location.longitude = None
                    location.save(
                        update_fields=[
                            "publisher",
                            "is_sharing",
                            "latitude",
                            "longitude",
                            "updated_at",
                        ]
                    )
                return Response(
                    self._serialize_location(order, location),
                    status=status.HTTP_200_OK,
                )

            location, _ = OrderLiveLocation.objects.update_or_create(
                order=order,
                defaults={
                    "publisher": request.user,
                    "latitude": values["latitude"],
                    "longitude": values["longitude"],
                    "is_sharing": True,
                },
            )
            return Response(
                self._serialize_location(order, location),
                status=status.HTTP_200_OK,
            )

    @classmethod
    def _serialize_location(cls, order, location):
        response_data = {
            "order_id": order.id,
            "is_sharing": bool(
                location.is_sharing
                and cls._is_location_eligible(order)
                and location.latitude is not None
                and location.longitude is not None
                and timezone.now() - location.updated_at
                <= LIVE_LOCATION_STALE_AFTER
            ),
            "latitude": None,
            "longitude": None,
            "updated_at": location.updated_at,
        }
        if response_data["is_sharing"]:
            response_data["latitude"] = float(location.latitude)
            response_data["longitude"] = float(location.longitude)
        return response_data

    @staticmethod
    def _get_authorized_order(request, pk, for_update=False):
        queryset = Order.objects.all()
        if for_update:
            queryset = queryset.select_for_update()
        if request.user.user_type == "customer":
            return queryset.filter(
                pk=pk,
                customer=request.user,
            ).first()
        if request.user.user_type == "shop_admin":
            return queryset.filter(
                pk=pk,
                shop__owner=request.user,
            ).first()
        return None

    @staticmethod
    def _is_location_eligible(order):
        return (
            order.pickup_mode == "vehicle"
            and order.status in LIVE_LOCATION_ORDER_STATUSES
        )


class OrderTrackingView(generics.RetrieveAPIView):
    serializer_class = OrderTrackingSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def retrieve(self, request, *args, **kwargs):
        order = get_object_or_404(
            Order,
            id=kwargs["pk"],
            customer=request.user,
        )

        if order.status == "pending_super_admin":
            tracking_steps = [
                {
                    "status": "pending_super_admin",
                    "label": "Waiting for Super Admin",
                },
                {
                    "status": "admin_approved",
                    "label": "Approved by Super Admin",
                },
                {
                    "status": "accepted",
                    "label": "Order Accepted",
                },
                {
                    "status": "confirmed",
                    "label": "Order Confirmed",
                },
                {
                    "status": "preparing",
                    "label": "Preparing",
                },
                {
                    "status": "ready",
                    "label": "Ready for Handoff",
                },
                {
                    "status": "completed",
                    "label": "Completed",
                },
            ]
        elif order.status == "admin_approved":
            tracking_steps = [
                {"status": "admin_approved", "label": "Approved by Super Admin"},
                {"status": "accepted", "label": "Order Accepted"},
                {"status": "confirmed", "label": "Order Confirmed"},
                {"status": "preparing", "label": "Preparing"},
                {"status": "ready", "label": "Ready for Handoff"},
                {"status": "completed", "label": "Completed"},
            ]
        elif order.status in {"rejected", "cancelled"}:
            tracking_steps = [
                {"status": "pending", "label": "Order Placed"},
                {
                    "status": order.status,
                    "label": (
                        "Order Rejected"
                        if order.status == "rejected"
                        else "Order Cancelled"
                    ),
                },
            ]
        else:
            tracking_steps = [
                {"status": "pending", "label": "Order Placed"},
                {"status": "accepted", "label": "Order Accepted"},
                {"status": "confirmed", "label": "Order Confirmed"},
                {"status": "preparing", "label": "Preparing"},
                {"status": "ready", "label": "Ready for Handoff"},
                {"status": "completed", "label": "Completed"},
            ]

        current_index = next(
            (
                index
                for index, step in enumerate(tracking_steps)
                if step["status"] == order.status
            ),
            len(tracking_steps) - 1
            if order.status in {"rejected", "cancelled"}
            else 0,
        )

        return Response(
            {
                "order_id": order.id,
                "status": order.status,
                "status_display": order.get_status_display(),
                "current_step": current_index,
                "tracking_steps": tracking_steps,
                "created_at": order.created_at,
                "updated_at": order.updated_at,
            },
            status=status.HTTP_200_OK,
        )
