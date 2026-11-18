from datetime import date
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, AuthenticationFailed
from django.db import IntegrityError, transaction
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from customers.age_verification import is_age_self_declared, is_age_verified

User = get_user_model()


class StrictDateOfBirthField(serializers.Field):
    def to_internal_value(self, value):
        if not isinstance(value, str):
            raise serializers.ValidationError(
                "Enter a date as a YYYY-MM-DD string."
            )
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise serializers.ValidationError(
                "Use YYYY-MM-DD format."
            )
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise serializers.ValidationError(
                "Enter a valid date."
            ) from None


class StrictDateOfBirthSerializer(serializers.Serializer):
    date_of_birth = StrictDateOfBirthField(required=False)
    age_self_declaration = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if "date_of_birth" not in attrs and "age_self_declaration" not in attrs:
            raise serializers.ValidationError(
                {"date_of_birth": "date_of_birth or age_self_declaration is required."}
            )
        if attrs.get("age_self_declaration") is False:
            raise serializers.ValidationError(
                {"age_self_declaration": "Only an affirmative declaration is supported."}
            )
        return attrs


class SetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        try:
            validate_password(attrs["password"], user=self.context["user"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"password": list(exc.messages)}
            ) from exc

        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmRequestSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=64)
    token = serializers.CharField(max_length=256)


class GoogleIdTokenSerializer(serializers.Serializer):
    id_token = serializers.CharField(
        max_length=8192,
        write_only=True,
        trim_whitespace=False,
    )

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["username", "email", "phone", "password"]

    def validate_password(self, value):
        candidate = User(
            username=self.initial_data.get("username", ""),
            email=self.initial_data.get("email", ""),
        )
        try:
            validate_password(value, user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError:
            raise serializers.ValidationError({"detail": "Unable to create an account with these details."}) from None
        return user


class ProfileSerializer(serializers.ModelSerializer):
    age_verified = serializers.SerializerMethodField()
    age_verified_at = serializers.SerializerMethodField()
    age_self_declared = serializers.SerializerMethodField()
    age_self_declared_at = serializers.SerializerMethodField()
    phone_verified = serializers.SerializerMethodField()
    verified_phone_last4 = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "phone",
            "phone_verified",
            "verified_phone_last4",
            "is_verified",
            "date_of_birth",
            "age_verified",
            "age_verified_at",
            "age_self_declared",
            "age_self_declared_at",
        ]
        read_only_fields = [
            "email",
            "phone",
            "phone_verified",
            "verified_phone_last4",
            "is_verified",
            "date_of_birth",
            "age_verified",
            "age_verified_at",
            "age_self_declared",
            "age_self_declared_at",
        ]

    def get_age_verified(self, obj):
        return is_age_verified(obj)

    def get_age_verified_at(self, obj):
        return obj.age_verified_at if is_age_verified(obj) else None

    def get_age_self_declared(self, obj):
        return is_age_self_declared(obj)

    def get_age_self_declared_at(self, obj):
        return obj.age_self_declared_at if is_age_self_declared(obj) else None

    def get_phone_verified(self, obj):
        from .contact_identity import current_phone_verified
        return current_phone_verified(obj)

    def get_verified_phone_last4(self, obj):
        identity = getattr(obj, "verified_phone_identity", None)
        return identity.phone_last4 if identity else None


class CustomerTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        if self.user.is_blocked:
            raise PermissionDenied("This customer account is blocked.")
        return data


class CustomerTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        refresh = RefreshToken(attrs["refresh"])
        user_id = refresh.get(api_settings.USER_ID_CLAIM)
        user = User.objects.filter(pk=user_id).first()
        if user is None or not user.is_active:
            raise AuthenticationFailed("No active account found for this token.")
        if user.is_blocked:
            raise PermissionDenied("This customer account is blocked.")
        return super().validate(attrs)
