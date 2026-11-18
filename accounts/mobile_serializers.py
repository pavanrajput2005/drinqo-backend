from django.utils import timezone
import re
from rest_framework import serializers

from .serializers import StrictDateOfBirthField


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"non_field_errors": ["Expected a JSON object."]})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({"non_field_errors": ["Unknown fields: " + ", ".join(sorted(unknown))]})
        for field in self.fields:
            if field in data and not isinstance(data[field], str):
                raise serializers.ValidationError({field: "Expected a string."})
        return super().to_internal_value(data)


class EmailSerializer(StrictSerializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        return value.strip().lower()


class EmailVerifySerializer(EmailSerializer):
    otp = serializers.RegexField(r"\A[0-9]{6}\Z", trim_whitespace=False, write_only=True)


class GoogleSerializer(StrictSerializer):
    id_token = serializers.CharField(max_length=8192, trim_whitespace=False, write_only=True)


class AppleSerializer(StrictSerializer):
    identity_token = serializers.CharField(max_length=8192, trim_whitespace=False, write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)


class MobileUserResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    birth_date = serializers.DateField(allow_null=True)
    mobile_number = serializers.CharField(allow_null=True)
    auth_provider = serializers.CharField()
    is_profile_complete = serializers.BooleanField()
    user_type = serializers.CharField()


class MobileLoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    is_profile_complete = serializers.BooleanField()
    user = MobileUserResponseSerializer()


class MobileProfileSerializer(StrictSerializer):
    birth_date = StrictDateOfBirthField(required=False)
    mobile_number = serializers.RegexField(r"\A[0-9]{10,15}\Z", required=False, trim_whitespace=False)

    def validate_birth_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("Birth date cannot be in the future.")
        return value

    def validate(self, attrs):
        user = self.context["user"]
        if not attrs:
            raise serializers.ValidationError("Provide birth_date or mobile_number.")
        if not attrs.get("birth_date", user.date_of_birth) or not attrs.get("mobile_number", user.phone):
            raise serializers.ValidationError("Birth date and mobile number are required to complete the profile.")
        if not re.fullmatch(r"[0-9]{10,15}", attrs.get("mobile_number", user.phone)):
            raise serializers.ValidationError({"mobile_number": "Use 10-15 digits."})
        if attrs.get("birth_date", user.date_of_birth) != user.date_of_birth:
            from customers.age_verification import has_approved_age_proof
            if user.age_verified or has_approved_age_proof(user):
                raise serializers.ValidationError("Verified birth date cannot be changed here.")
        return attrs
