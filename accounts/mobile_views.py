"""Additive Flutter authentication contract; existing account routes stay intact."""
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_post_parameters
from django.utils.decorators import method_decorator
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema

from .apple_identity import (
    AppleVerificationUnavailable, InvalidAppleIdentity, verify_apple_identity,
)
from .authentication import JWTAuthentication
from .google_identity import (
    GoogleVerificationUnavailable, InvalidGoogleIdentity, verify_google_id_token,
)
from .mobile_serializers import (
    AppleSerializer, EmailSerializer, EmailVerifySerializer,
    GoogleSerializer, MobileProfileSerializer, MobileLoginResponseSerializer, MobileUserResponseSerializer,
)
from .models import EmailAuthState, EmailOTP, User
from .throttles import FailClosedLoginRateThrottle


def user_data(user):
    return {
        "id": user.pk, "email": user.email,
        "first_name": user.first_name, "last_name": user.last_name,
        "birth_date": user.date_of_birth.isoformat() if user.date_of_birth else None,
        "mobile_number": user.phone or None,
        "auth_provider": user.auth_provider,
        "is_profile_complete": user.is_profile_complete,
        "user_type": user.user_type,
    }


def token_response(user):
    if not user.is_active or user.is_blocked or user.user_type not in {"customer", "shop_admin", "super_admin"}:
        raise AuthenticationFailed("Unable to sign in with this account.")
    refresh = RefreshToken.for_user(user)
    return Response({
        "access": str(refresh.access_token), "refresh": str(refresh),
        "is_profile_complete": user.is_profile_complete, "user": user_data(user),
    })


def error(message, status=400):
    return Response({"error": message}, status=status)


def first_error(data):
    if isinstance(data, dict):
        field, value = next(iter(data.items()))
        message = first_error(value)
        return message if field in ("detail", "non_field_errors") else f"{field}: {message}"
    if isinstance(data, list):
        return first_error(data[0]) if data else "Invalid request."
    return str(data)


@method_decorator(sensitive_post_parameters("otp", "id_token", "identity_token", "password", "confirm_password", "current_password"), name="dispatch")
class MobileAPIView(APIView):
    parser_classes = [JSONParser]
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [FailClosedLoginRateThrottle]

    def dispatch(self, request, *args, **kwargs):
        from orders.handover_privacy import protect_handover_request
        protect_handover_request(request)
        return super().dispatch(request, *args, **kwargs)

    def get_authenticate_header(self, request):
        return 'Bearer realm="api"'

    def handle_exception(self, exc):
        if isinstance(exc, DatabaseError):
            return error("Authentication storage is temporarily unavailable.", 503)
        response = super().handle_exception(exc)
        response.data = {"error": first_error(response.data)}
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        response.sensitive_response = True
        return response


def social_user(provider, subject, email, first_name="", last_name=""):
    field = "google_sub" if provider == "google" else "apple_sub"
    # Identity belongs to its verified provider subject, never just a matching
    # email. Existing accounts must explicitly link through the established flow.
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(**{field: subject}).first()
            if user is not None:
                return user
            if not email:
                raise AuthenticationFailed("A verified email is required on first sign-in.")
            if User.objects.filter(email__iexact=email).exists():
                raise AuthenticationFailed("Sign in with your existing method and link this provider first.")
            user = User(
                email=email, username=f"{provider}_{uuid.uuid4().hex}",
                first_name=first_name, last_name=last_name,
                auth_provider=provider, email_verified=True, **{field: subject},
            )
            user.set_unusable_password()
            user.save(force_insert=True)
            return user
    except IntegrityError:
        user = User.objects.filter(**{field: subject}).first()
        if user is None:
            raise AuthenticationFailed("Unable to sign in; use your existing sign-in method.") from None
        return user


class MobileGoogleView(MobileAPIView):
    throttle_scope = "google_login"

    @extend_schema(request=GoogleSerializer, responses=MobileLoginResponseSerializer)
    def post(self, request):
        data = GoogleSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            identity = verify_google_id_token(data.validated_data["id_token"])
        except InvalidGoogleIdentity:
            return error("Invalid or expired Google token.", 401)
        except GoogleVerificationUnavailable:
            return error("Google verification is temporarily unavailable.", 503)
        return token_response(social_user(
            "google", identity.subject, identity.email,
            identity.first_name, identity.last_name,
        ))


class MobileAppleView(MobileAPIView):
    throttle_scope = "apple_login"

    @extend_schema(request=AppleSerializer, responses=MobileLoginResponseSerializer)
    def post(self, request):
        data = AppleSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            subject, email = verify_apple_identity(data.validated_data["identity_token"])
        except InvalidAppleIdentity:
            return error("Invalid or expired Apple token.", 401)
        except AppleVerificationUnavailable:
            return error("Apple verification is temporarily unavailable.", 503)
        return token_response(social_user(
            "apple", subject, email, data.validated_data.get("first_name", ""),
            data.validated_data.get("last_name", ""),
        ))


def locked_email_state(email):
    digest = salted_hmac("mobile-email-auth", email, algorithm="sha256").hexdigest()
    EmailAuthState.objects.get_or_create(email_digest=digest)
    return EmailAuthState.objects.select_for_update().get(email_digest=digest)


class EmailSendOTPView(MobileAPIView):
    throttle_scope = "email_otp_send"
    purpose = "login"

    def post(self, request):
        data = EmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email = data.validated_data["email"]
        # Lock survives OTP replacement/consumption, preventing resend-based
        # brute-force resets. Database failure rolls back the entire issuance.
        with transaction.atomic():
            state = locked_email_state(email)
            now = timezone.now()
            if state.last_sent_at and now - state.last_sent_at < timedelta(seconds=60):
                return error("Wait 60 seconds before requesting another OTP.", 429)
            if not state.send_window or now - state.send_window >= timedelta(hours=1):
                state.send_window, state.send_count = now, 0
            if state.send_count >= 5:
                return error("Too many OTP requests. Try again later.", 429)
            code = f"{secrets.randbelow(1_000_000):06d}"
            EmailOTP.objects.filter(email=email).delete()
            record = EmailOTP(email=email, purpose=self.purpose,
                              requesting_user=request.user if self.purpose == "link" else None)
            record.set_code(code)
            record.save()
            try:
                sent = send_mail(
                    "Your DRINQO sign-in code",
                    f"Your DRINQO code is {code}. It expires in 5 minutes. Do not share this sign-in code.",
                    settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False,
                )
                if sent != 1:
                    raise OSError("Email was not accepted")
            except Exception:
                transaction.set_rollback(True)
                return error("Unable to send OTP. Please try again later.", 503)
            state.send_count += 1
            state.last_sent_at = now
            state.save()
        return Response({"message": "OTP sent successfully"})


class EmailVerifyOTPView(MobileAPIView):
    throttle_scope = "email_otp_verify"

    @extend_schema(request=EmailVerifySerializer, responses=MobileLoginResponseSerializer)
    def post(self, request):
        data = EmailVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email = data.validated_data["email"]
        with transaction.atomic():
            state = locked_email_state(email)
            now = timezone.now()
            if not state.verify_window or now - state.verify_window >= timedelta(hours=1):
                state.verify_window, state.verify_count = now, 0
            if state.verify_count >= 10:
                return error("Too many verification attempts. Try again later.", 429)
            state.verify_count += 1
            state.save()
            record = EmailOTP.objects.select_for_update().filter(email=email).first()
            if record is None or record.purpose != "login" or not record.is_valid() or not record.check_code(data.validated_data["otp"]):
                return error("Invalid or expired OTP.")
            user = User.objects.select_for_update().filter(email__iexact=email).first()
            if user is not None and (not user.is_active or user.is_blocked or user.user_type != "customer"):
                record.delete()
                return error("Unable to sign in with this account.", 401)
            if user is None:
                try:
                    with transaction.atomic():
                        user = User(email=email, username=f"email_{uuid.uuid4().hex}", email_verified=True)
                        user.set_unusable_password()
                        user.save(force_insert=True)
                except IntegrityError:
                    # A simultaneous social signup must never promote privileges.
                    user = User.objects.select_for_update().get(email__iexact=email)
                    if not user.is_active or user.is_blocked or user.user_type != "customer":
                        record.delete()
                        return error("Unable to sign in with this account.", 401)
            if not user.email_verified:
                # Mailbox proof alone cannot vouch for credentials already
                # enrolled on this row. Do not merge trust or retire sessions
                # implicitly; password users retain the existing reset flow.
                if user.has_usable_password() or user.google_sub or user.apple_sub or user.firebase_uid:
                    record.delete()
                    return error("Use your existing sign-in method or account recovery.", 409)
                user.email_verified = True
                user.save(update_fields=["email_verified"])
            record.delete()
            return token_response(user)


class MobileProfileView(MobileAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_scope = "mobile_profile"

    @extend_schema(request=MobileProfileSerializer, responses=MobileUserResponseSerializer)
    def patch(self, request):
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if user.is_blocked:
                return error("This account is blocked.", 403)
            data = MobileProfileSerializer(data=request.data, context={"user": user})
            data.is_valid(raise_exception=True)
            changes = data.validated_data
            # Editable contact data grants no new phone-login identity. The old
            # VerifiedPhoneIdentity remains unchanged for retention/support review.
            if "birth_date" in changes and changes["birth_date"] != user.date_of_birth:
                user.age_self_declared = False
                user.age_self_declared_at = None
                user.age_verified = False
                user.age_verified_at = None
            for field, value in changes.items():
                setattr(user, field, value)
            user.is_profile_complete = True
            user.save()
            # The old customer-profile endpoint renders a separate model. Keep
            # existing rows consistent without inventing checkout address data.
            from customers.models import CustomerProfile
            profile_updates = {}
            if "birth_date" in changes:
                profile_updates["date_of_birth"] = user.date_of_birth
            if "mobile_number" in changes:
                profile_updates["phone"] = user.phone
            if profile_updates:
                CustomerProfile.objects.filter(user=user).update(
                    **profile_updates, updated_at=timezone.now(),
                )
        return Response(user_data(user))


def legacy_phone_account(user):
    return (user.email.endswith("@otp.drinqo.invalid") and
            hasattr(user, "verified_phone_identity") and not user.email_verified)


class LinkEmailSendView(EmailSendOTPView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    purpose = "link"

    def post(self, request):
        if not legacy_phone_account(request.user):
            return error("This migration flow is only for an existing phone-only account.", 409)
        return super().post(request)


class LinkEmailVerifyView(MobileAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_scope = "email_otp_verify"

    @extend_schema(request=EmailVerifySerializer, responses=MobileLoginResponseSerializer)
    def post(self, request):
        data = EmailVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email = data.validated_data["email"]
        with transaction.atomic():
            state = locked_email_state(email)
            now = timezone.now()
            if not state.verify_window or now - state.verify_window >= timedelta(hours=1):
                state.verify_window, state.verify_count = now, 0
            if state.verify_count >= 10:
                return error("Too many verification attempts. Try again later.", 429)
            state.verify_count += 1
            state.save()
            record = EmailOTP.objects.select_for_update().filter(email=email).first()
            if (record is None or record.purpose != "link" or record.requesting_user_id != request.user.pk
                    or not record.is_valid() or not record.check_code(data.validated_data["otp"])):
                return error("Invalid or expired OTP.")
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if not legacy_phone_account(user) or not user.is_active or user.is_blocked:
                return error("Unable to migrate this account.", 409)
            if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                return error("Unable to link this email. Use account recovery.", 409)
            try:
                with transaction.atomic():
                    user.email, user.email_verified = email, True
                    user.save(update_fields=["email", "email_verified"])
            except IntegrityError:
                return error("Unable to link this email. Use account recovery.", 409)
            from customers.models import CustomerProfile
            CustomerProfile.objects.filter(user=user).update(email=email)
            record.delete()
            return token_response(user)


class SetAccountPasswordView(MobileAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_scope = "password_reset_confirm"

    def post(self, request):
        from .serializers import SetPasswordSerializer
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
        if not isinstance(request.data, dict):
            return error("Expected a JSON object.")
        if "current_password" in request.data and not isinstance(request.data["current_password"], str):
            return error("Current password must be a string.")
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if not user.email_verified or user.email.endswith("@otp.drinqo.invalid"):
                return error("Link a verified email before setting a password.", 403)
            if user.has_usable_password() and not user.check_password(request.data.get("current_password", "")):
                return error("Current password is required and must be correct.", 400)
            data = SetPasswordSerializer(data=request.data, context={"user": user})
            data.is_valid(raise_exception=True)
            user.set_password(data.validated_data["password"])
            user.save(update_fields=["password"])
            for outstanding in OutstandingToken.objects.select_for_update().filter(user=user):
                BlacklistedToken.objects.get_or_create(token=outstanding)
        return Response({"detail": "Password set successfully. Sign in again."})
