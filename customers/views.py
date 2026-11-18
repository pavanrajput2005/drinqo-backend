from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.authentication import JWTAuthentication
from accounts.permissions import CustomerOnly
from customers.checkout_eligibility import customer_checkout_eligibility
from customers.age_verification import has_approved_age_proof, is_age_verified
from customers.verification_constants import AGE_PROOF_DOCUMENT_TYPE
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from shops.geofence import distance_between_coordinates
from shops.permissions import (
    shop_can_accept_new_orders,
    shops_accepting_new_orders,
)

from .models import (
    AccountDeletionRequest,
    CustomerProfile,
    CustomerVerificationSession,
    CustomerVehicle,
    FavoriteShop,
    Cart,
    CartItem,
    LegalPolicy,
    Notification,
    SupportTicket,
    UserNotificationPreference,
    UserPushToken,
)

from .serializers import (
    CustomerProfileSerializer,
    CustomerVehicleSerializer,
    FavoriteShopSerializer,
    CartSerializer,
    CartItemSerializer,
    CartItemAddRequestSerializer,
    CartItemQuantityRequestSerializer,
    CheckoutRequestSerializer,
    CheckoutPreviewSerializer,
    AccountDeletionRequestSerializer,
    CustomerVerificationSessionSerializer,
    LegalPolicySerializer,
    NotificationSerializer,
    SupportTicketSerializer,
    UserNotificationPreferenceSerializer,
    UserPushTokenSerializer,
)
from .push_tokens import register_push_token

ACCOUNT_DELETION_GRACE_PERIOD = timedelta(days=30)
MINIMUM_ORDER_AMOUNT = Decimal("500.00")
APPLICATION_FEE = Decimal("50.00")
PICKUP_RADIUS_METERS = 100


def _require_super_admin(user):
    if user.user_type != "super_admin":
        raise PermissionDenied("Super Admin access required.")


class CustomerProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = CustomerProfileSerializer
    permission_classes = [CustomerOnly]

    def get_object(self):
        profile, created = CustomerProfile.objects.get_or_create(
            user=self.request.user,
            defaults={
                "full_name": self.request.user.get_full_name()
                or self.request.user.username,
                "phone": self.request.user.phone,
                "email": self.request.user.email,
                "address_line1": "",
                "address_line2": "",
                "city": "",
                "state": "",
                "postal_code": "",
            },
        )

        return profile

    def perform_update(self, serializer):
        with transaction.atomic():
            user = type(self.request.user).objects.select_for_update().get(
                pk=self.request.user.pk
            )
            requested_dob = serializer.validated_data.get("date_of_birth", user.date_of_birth)
            if requested_dob != user.date_of_birth and has_approved_age_proof(user):
                raise PermissionDenied(
                    "Date of birth cannot be changed after age proof approval."
                )

            profile = serializer.save()
            if requested_dob != user.date_of_birth:
                user.date_of_birth = requested_dob
                user.age_verified = False
                user.age_verified_at = None
                user.age_self_declared = False
                user.age_self_declared_at = None
                user.save(update_fields=[
                    "date_of_birth", "age_verified", "age_verified_at",
                    "age_self_declared", "age_self_declared_at",
                ])

            required_fields = [
                profile.full_name,
                profile.phone or user.phone,
                profile.address_line1,
                profile.city,
                profile.state,
                profile.postal_code,
            ]
            completed = all(str(field or "").strip() for field in required_fields)
            if completed and profile.profile_completed_at is None:
                profile.profile_completed_at = timezone.now()
                profile.save(update_fields=["profile_completed_at"])
            elif not completed and profile.profile_completed_at is not None:
                profile.profile_completed_at = None
                profile.save(update_fields=["profile_completed_at"])


class CustomerVehicleListCreateView(generics.ListCreateAPIView):
    serializer_class = CustomerVehicleSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return CustomerVehicle.objects.filter(
            customer__user=self.request.user
        ).order_by("-created_at")

    def perform_create(self, serializer):
        profile, created = CustomerProfile.objects.get_or_create(
            user=self.request.user,
            defaults={
                "full_name": self.request.user.get_full_name()
                or self.request.user.username,
                "phone": self.request.user.phone,
                "email": self.request.user.email,
                "address_line1": "",
                "address_line2": "",
                "city": "",
                "state": "",
                "postal_code": "",
            },
        )

        serializer.save(customer=profile)


class CustomerVehicleDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CustomerVehicleSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return CustomerVehicle.objects.filter(
            customer__user=self.request.user
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        vehicle = get_object_or_404(self.get_queryset().select_for_update(of=("self",)), pk=kwargs["pk"])
        if vehicle.orders.exclude(status__in=("completed", "cancelled", "rejected")).exists():
            return Response({"detail": "Vehicle is selected for an active order."}, status=409)
        self.perform_destroy(vehicle)
        return Response(status=204)


class FavoriteShopListCreateView(generics.ListCreateAPIView):
    serializer_class = FavoriteShopSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return FavoriteShop.objects.filter(
            customer__user=self.request.user,
            shop__in=shops_accepting_new_orders(),
        ).select_related(
            "shop"
        ).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile, created = CustomerProfile.objects.get_or_create(
            user=self.request.user,
            defaults={
                "full_name": self.request.user.get_full_name()
                or self.request.user.username,
                "phone": self.request.user.phone,
                "email": self.request.user.email,
                "address_line1": "",
                "address_line2": "",
                "city": "",
                "state": "",
                "postal_code": "",
            },
        )

        favorite, was_created = FavoriteShop.objects.get_or_create(
            customer=profile,
            shop=serializer.validated_data["shop"],
        )
        return Response(
            self.get_serializer(favorite).data,
            status=status.HTTP_201_CREATED if was_created else status.HTTP_200_OK,
        )


class FavoriteShopDeleteView(generics.DestroyAPIView):
    serializer_class = FavoriteShopSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return FavoriteShop.objects.filter(
            customer__user=self.request.user,
        )


class CustomerCartView(generics.RetrieveAPIView):
    serializer_class = CartSerializer
    permission_classes = [CustomerOnly]

    def get_object(self):
        profile, created = CustomerProfile.objects.get_or_create(
            user=self.request.user,
            defaults={
                "full_name": self.request.user.get_full_name()
                or self.request.user.username,
                "phone": self.request.user.phone,
                "email": self.request.user.email,
                "address_line1": "",
                "address_line2": "",
                "city": "",
                "state": "",
                "postal_code": "",
            },
        )

        cart, created = Cart.objects.get_or_create(
            customer=profile
        )

        item_count = cart.items.count()

        print(
            "🛒 GET CART:",
            "user_id=",
            self.request.user.id,
            "cart_id=",
            cart.id,
            "item_count=",
            item_count,
        )

        return cart


class CartItemAddView(generics.CreateAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [CustomerOnly]

    def create(self, request, *args, **kwargs):
        from products.models import Product

        input_serializer = CartItemAddRequestSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        product_id = input_serializer.validated_data["product_id"]
        quantity = input_serializer.validated_data["quantity"]

        # ---------------------------------------------------------
        # PRODUCT
        # ---------------------------------------------------------
        try:
            product = Product.objects.select_related(
                "shop"
            ).get(
                id=product_id,
                is_active=True,
            )
        except Product.DoesNotExist:
            return Response(
                {
                    "detail": "Product not found or unavailable."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not shop_can_accept_new_orders(product.shop):
            return Response(
                {"detail": "Product not found or unavailable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if quantity > product.stock_quantity:
            return Response(
                {
                    "detail": (
                        "Requested quantity exceeds "
                        "available stock."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------------------
        # SAME CUSTOMER PROFILE
        # ---------------------------------------------------------
        profile, created = CustomerProfile.objects.get_or_create(
            user=request.user,
            defaults={
                "full_name": request.user.get_full_name()
                or request.user.username,
                "phone": request.user.phone,
                "email": request.user.email,
                "address_line1": "",
                "address_line2": "",
                "city": "",
                "state": "",
                "postal_code": "",
            },
        )

        # ---------------------------------------------------------
        # ATOMIC CART OPERATION
        # ---------------------------------------------------------
        with transaction.atomic():

            # Cart is OneToOne with CustomerProfile.
            # This is the same cart used by CustomerCartView
            # and OrderCreateView.
            cart, created = Cart.objects.select_for_update().get_or_create(
                customer=profile
            )

            # -----------------------------------------------------
            # ONE SHOP PER CART
            # -----------------------------------------------------
            existing_item = (
                CartItem.objects
                .filter(cart=cart)
                .exclude(product=product)
                .select_related("product__shop")
                .first()
            )

            if existing_item:
                if (
                    existing_item.product.shop_id
                    != product.shop_id
                ):
                    return Response(
                        {
                            "detail": (
                                "Your cart already contains "
                                "products from another shop. "
                                "Please clear your cart before "
                                "shopping from this shop."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            # -----------------------------------------------------
            # LOCK EXISTING CART ITEM
            # -----------------------------------------------------
            cart_item = (
                CartItem.objects
                .select_for_update()
                .filter(
                    cart=cart,
                    product=product,
                )
                .first()
            )

            if cart_item is None:
                cart_item = CartItem.objects.create(
                    cart=cart,
                    product=product,
                    quantity=quantity,
                )

                response_status = status.HTTP_201_CREATED

            else:
                new_quantity = (
                    cart_item.quantity + quantity
                )

                if new_quantity > product.stock_quantity:
                    return Response(
                        {
                            "detail": (
                                "Requested quantity exceeds "
                                "available stock."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                cart_item.quantity = new_quantity

                cart_item.save(
                    update_fields=[
                        "quantity",
                        "updated_at",
                    ]
                )

                response_status = status.HTTP_200_OK

            # -----------------------------------------------------
            # FINAL CART ITEM COUNT
            # -----------------------------------------------------
            item_count = CartItem.objects.filter(
                cart=cart
            ).count()

            print(
                "🛒 ADD CART:",
                "user_id=",
                request.user.id,
                "cart_id=",
                cart.id,
                "item_count=",
                item_count,
            )

        # ---------------------------------------------------------
        # RESPONSE ONLY AFTER SUCCESSFUL TRANSACTION
        # ---------------------------------------------------------
        return Response(
            self.get_serializer(cart_item).data,
            status=response_status,
        )


class CartItemUpdateView(generics.UpdateAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [CustomerOnly]
    http_method_names = ["patch"]

    def get_queryset(self):
        return CartItem.objects.filter(
            cart__customer__user=self.request.user
        ).select_related(
            "product",
            "product__shop",
        )

    def update(self, request, *args, **kwargs):
        input_serializer = CartItemQuantityRequestSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        quantity = input_serializer.validated_data["quantity"]
        with transaction.atomic():
            # Cart is the parent synchronization point also held by checkout.
            cart = Cart.objects.select_for_update().filter(
                customer__user=request.user,
            ).first()
            if cart is None:
                raise Http404
            cart_item = (
                CartItem.objects.select_for_update()
                .select_related("product__shop")
                .filter(pk=kwargs["pk"], cart=cart)
                .first()
            )
            if cart_item is None:
                raise Http404
            product_model = type(cart_item.product)
            product = product_model.objects.select_for_update().select_related(
                "shop"
            ).get(pk=cart_item.product_id)
            if not product.is_active:
                return Response(
                    {"detail": "Product is no longer available."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not shop_can_accept_new_orders(product.shop):
                return Response(
                    {"detail": "Shop is currently unavailable."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if quantity > product.stock_quantity:
                return Response(
                    {"detail": "Requested quantity exceeds available stock."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cart_item.quantity = quantity
            cart_item.save(update_fields=["quantity", "updated_at"])

        return Response(
            self.get_serializer(cart_item).data
        )


class CartItemDeleteView(generics.DestroyAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return CartItem.objects.filter(
            cart__customer__user=self.request.user
        )


@extend_schema(request=CheckoutRequestSerializer, responses={200: CheckoutPreviewSerializer})
class CheckoutPreviewView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def post(self, request):
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

        # ---------------------------------------------------------
        # CUSTOMER PROFILE
        # ---------------------------------------------------------
        # ---------------------------------------------------------
        # CART
        # ---------------------------------------------------------
        cart = Cart.objects.filter(
            customer=profile
        ).first()

        if not cart:
            return Response(
                {
                    "detail": "Cart is empty."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------------------
        # CART ITEMS
        # ---------------------------------------------------------
        cart_items = list(
            cart.items.select_related(
                "product",
                "product__shop",
            ).all()
        )

        if not cart_items:
            return Response(
                {
                    "detail": "Cart is empty."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        pickup_mode = request_values["pickup_mode"]
        customer_latitude = request_values["latitude"]
        customer_longitude = request_values["longitude"]

        vehicle = None

        # Vehicle is required only for vehicle pickup.
        if pickup_mode == "vehicle":
            vehicle = CustomerVehicle.objects.filter(
                id=request_values["vehicle_id"],
                customer=profile,
            ).first()

            if not vehicle:
                return Response(
                    {
                        "detail": "Vehicle not found."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # ---------------------------------------------------------
        # VALIDATE CART ITEMS
        # ---------------------------------------------------------
        first_shop = None
        preview_items = []

        total_items = 0
        subtotal = Decimal("0.00")

        for item in cart_items:
            product = item.product
            shop = product.shop

            if not product.is_active:
                return Response(
                    {
                        "detail": (
                            f"Product '{product.name}' "
                            "is no longer available."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not shop_can_accept_new_orders(shop):
                return Response(
                    {
                        "detail": (
                            f"Shop '{shop.shop_name}' "
                            "is currently unavailable."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

            if item.quantity > product.stock_quantity:
                return Response(
                    {
                        "detail": (
                            f"Only {product.stock_quantity} "
                            f"units of '{product.name}' "
                            "are available."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            item_subtotal = (
                product.price * item.quantity
            )

            total_items += item.quantity
            subtotal += item_subtotal

            preview_items.append(
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "quantity": item.quantity,
                    "unit_price": product.price,
                    "subtotal": item_subtotal,
                }
            )

        # ---------------------------------------------------------
        # ₹500 minimum applies to product subtotal, before fees.
        # ---------------------------------------------------------
        distance_to_shop_m = None
        if (
            first_shop.latitude is not None
            and first_shop.longitude is not None
        ):
            distance_to_shop_m = distance_between_coordinates(
                customer_latitude,
                customer_longitude,
                first_shop.latitude,
                first_shop.longitude,
            )
        within_pickup_radius = (
            distance_to_shop_m is not None
            and distance_to_shop_m <= PICKUP_RADIUS_METERS
        )
        can_checkout = (
            subtotal >= MINIMUM_ORDER_AMOUNT
            and within_pickup_radius
        )
        application_fee = APPLICATION_FEE
        delivery_fee = Decimal("0.00")

        total_amount = (
            subtotal
            + application_fee
            + delivery_fee
        )

        vehicle_data = None

        if vehicle:
            vehicle_data = {
                "id": vehicle.id,
                "vehicle_type": vehicle.vehicle_type,
                "vehicle_number": vehicle.vehicle_number,
                "vehicle_name": vehicle.vehicle_name,
            }

        if subtotal < MINIMUM_ORDER_AMOUNT:
            detail = "Minimum product subtotal is ₹500 to place an order."
        elif distance_to_shop_m is None:
            detail = "Shop location is unavailable; checkout cannot be validated."
        elif not within_pickup_radius:
            detail = "You must be within 100 meters of the shop to check out."
        else:
            detail = None

        preview_data = {
            "can_checkout": can_checkout,
            **({"detail": detail} if detail else {}),
            "shop_id": first_shop.id,
            "shop_name": first_shop.shop_name,
            "items": preview_items,
            "total_items": total_items,
            "subtotal": subtotal,
            "minimum_order_amount": MINIMUM_ORDER_AMOUNT,
            "distance_to_shop_m": distance_to_shop_m,
            "pickup_radius_m": PICKUP_RADIUS_METERS,
            "application_fee": application_fee,
            "platform_fee": application_fee,
            "delivery_fee": delivery_fee,
            "total_amount": total_amount,
            "pickup_mode": pickup_mode,
            "vehicle": vehicle_data,
        }

        serializer = CheckoutPreviewSerializer(
            preview_data
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return Notification.objects.filter(
            user=self.request.user
        ).select_related("order")


class NotificationReadView(generics.GenericAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [CustomerOnly]

    def post(self, request, *args, **kwargs):
        notification = get_object_or_404(
            Notification,
            id=kwargs["pk"],
            user=request.user,
        )

        notification.is_read = True

        notification.save(
            update_fields=["is_read"]
        )

        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_200_OK,
        )


class CustomerVerificationSessionListCreateView(generics.ListCreateAPIView):
    serializer_class = CustomerVerificationSessionSerializer
    permission_classes = [CustomerOnly]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return CustomerVerificationSession.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document_type = serializer.validated_data["document_type"]
        try:
            with transaction.atomic():
                # Lock the customer before checking/creating the partial-unique row.
                user = type(request.user).objects.select_for_update().get(pk=request.user.pk)
                if document_type == AGE_PROOF_DOCUMENT_TYPE:
                    if is_age_verified(user):
                        return Response(
                            {"detail": "Age verification is already complete."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    if CustomerVerificationSession.objects.select_for_update().filter(
                        user=user,
                        document_type=AGE_PROOF_DOCUMENT_TYPE,
                        status="pending",
                    ).exists():
                        return Response(
                            {"detail": "An age-proof verification is already pending."},
                            status=status.HTTP_409_CONFLICT,
                        )
                session = serializer.save(user=user)
        except IntegrityError:
            # PostgreSQL partial unique constraint is the final guard when
            # concurrent requests race after validation.
            if document_type == AGE_PROOF_DOCUMENT_TYPE:
                return Response(
                    {"detail": "An age-proof verification is already pending."},
                    status=status.HTTP_409_CONFLICT,
                )
            raise
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class CustomerVerificationSessionDetailView(generics.RetrieveAPIView):
    serializer_class = CustomerVerificationSessionSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return CustomerVerificationSession.objects.filter(user=self.request.user)


class SupportTicketListCreateView(generics.ListCreateAPIView):
    serializer_class = SupportTicketSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return SupportTicket.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class SupportTicketDetailView(generics.RetrieveAPIView):
    serializer_class = SupportTicketSerializer
    permission_classes = [CustomerOnly]

    def get_queryset(self):
        return SupportTicket.objects.filter(user=self.request.user)


class AccountDeletionRequestView(APIView):
    permission_classes = [CustomerOnly]

    def get(self, request):
        deletion_request = AccountDeletionRequest.objects.filter(
            user=request.user,
        ).first()
        if deletion_request is None:
            return Response({"status": "not_requested"})
        return Response(AccountDeletionRequestSerializer(deletion_request).data)

    def post(self, request):
        with transaction.atomic():
            # Lock the parent so an empty request set cannot race into two
            # pending requests. Existing historical duplicates remain intact.
            type(request.user).objects.select_for_update().get(pk=request.user.pk)
            existing = AccountDeletionRequest.objects.select_for_update().filter(
                user=request.user, status="pending"
            ).order_by("requested_at", "id").first()
            if existing is not None:
                return Response(
                    AccountDeletionRequestSerializer(existing).data,
                    status=status.HTTP_200_OK,
                )
            deletion_request = AccountDeletionRequest.objects.create(
                user=request.user,
                scheduled_for=timezone.now() + ACCOUNT_DELETION_GRACE_PERIOD,
            )
        return Response(
            AccountDeletionRequestSerializer(deletion_request).data,
            status=status.HTTP_201_CREATED,
        )


class AccountDeletionRequestCancelView(APIView):
    permission_classes = [CustomerOnly]

    def post(self, request):
        with transaction.atomic():
            type(request.user).objects.select_for_update().get(pk=request.user.pk)
            deletion_request = AccountDeletionRequest.objects.select_for_update().filter(
                user=request.user, status="pending"
            ).order_by("requested_at", "id").first()
            if deletion_request is None:
                return Response({"status": "not_requested"})
            if timezone.now() >= deletion_request.scheduled_for:
                return Response(
                    {"detail": "The cancellation window has expired."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            AccountDeletionRequest.objects.select_for_update().filter(
                user=request.user, status="pending"
            ).update(status="cancelled", cancelled_at=timezone.now())
            # ``QuerySet.update()`` doesn't update the already locked model
            # instance returned to the API client.
            deletion_request.refresh_from_db(fields=["status", "cancelled_at"])
        return Response(AccountDeletionRequestSerializer(deletion_request).data)


class UserNotificationPreferenceView(APIView):
    permission_classes = [CustomerOnly]

    def get(self, request):
        preferences, _ = UserNotificationPreference.objects.get_or_create(
            user=request.user,
        )
        return Response(UserNotificationPreferenceSerializer(preferences).data)

    def patch(self, request):
        preferences, _ = UserNotificationPreference.objects.get_or_create(
            user=request.user,
        )
        serializer = UserNotificationPreferenceSerializer(
            preferences,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class UserPushTokenView(APIView):
    permission_classes = [CustomerOnly]

    def post(self, request):
        serializer = UserPushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]
        device, created = register_push_token(
            user=request.user,
            token=token,
            platform=serializer.validated_data["platform"],
        )
        return Response(
            {"id": device.id, "platform": device.platform, "registered": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        token = request.data.get("token")
        if not isinstance(token, str) or not token.strip():
            return Response(
                {"token": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deleted, _ = UserPushToken.objects.filter(
            user=request.user,
            token=token,
        ).delete()
        if not deleted:
            raise Http404
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerLegalPolicyListView(generics.ListAPIView):
    serializer_class = LegalPolicySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return LegalPolicy.objects.filter(is_published=True)


class CustomerMarkAllNotificationsReadView(APIView):
    permission_classes = [CustomerOnly]

    def post(self, request):
        updated = Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).update(is_read=True)
        return Response({"updated_count": updated})
