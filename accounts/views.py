import logging
import uuid

from .authentication import JWTAuthentication
from .permissions import CustomerOnly

from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.db import DatabaseError, IntegrityError, transaction
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils import timezone

from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from .serializers import (
    CustomerTokenObtainPairSerializer,
    CustomerTokenRefreshSerializer,
    StrictDateOfBirthSerializer,
    RegisterSerializer,
    ProfileSerializer,
    SetPasswordSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmRequestSerializer,
    GoogleIdTokenSerializer,
)
from .google_identity import (
    GoogleVerificationUnavailable,
    InvalidGoogleIdentity,
    verify_google_id_token,
)
from .forms import VerifiedEmailPasswordResetForm
from .throttles import FailClosedLoginRateThrottle
from customers.age_verification import (
    age_proof_status,
    has_approved_age_proof,
    is_at_least_minimum_age,
    is_age_self_declared,
    is_age_verified,
)
User = get_user_model()
logger = logging.getLogger(__name__)


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer


class CustomerTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomerTokenObtainPairSerializer
    throttle_classes = [FailClosedLoginRateThrottle]
    throttle_scope = "password_login"


class CustomerTokenRefreshView(TokenRefreshView):
    serializer_class = CustomerTokenRefreshSerializer


class PasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_request"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        form = VerifiedEmailPasswordResetForm(
            data={"email": serializer.validated_data["email"]}
        )
        if (
            form.is_valid()
            and settings.PASSWORD_RESET_URL
            and settings.DEFAULT_FROM_EMAIL
        ):
            form.save(
                domain_override=request.get_host(),
                subject_template_name="accounts/password_reset_subject.txt",
                email_template_name="accounts/password_reset_email.txt",
                use_https=request.is_secure(),
                from_email=settings.DEFAULT_FROM_EMAIL,
                request=request,
                extra_email_context={
                    "reset_url": settings.PASSWORD_RESET_URL.rstrip("/"),
                    "timeout_seconds": settings.PASSWORD_RESET_TIMEOUT,
                },
            )

        return Response(
            {
                "detail": (
                    "If an account with that email is eligible, password "
                    "reset instructions will be sent."
                )
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    invalid_link_response = {
        "detail": "Invalid or expired password reset link."
    }

    def post(self, request):
        request_serializer = PasswordResetConfirmRequestSerializer(
            data=request.data
        )
        request_serializer.is_valid(raise_exception=True)
        uid = request_serializer.validated_data["uid"]
        token = request_serializer.validated_data["token"]

        try:
            user_id = int(force_str(urlsafe_base64_decode(uid)))
            # The active User uses a positive BigAutoField primary key. Reject
            # malformed/out-of-range IDs before they reach ORM/driver coercion.
            if not 1 <= user_id <= 9223372036854775807:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return Response(
                self.invalid_link_response,
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            user = (
                User.objects.select_for_update()
                .filter(pk=user_id)
                .first()
            )
            if (
                user is None
                or not user.is_active
                or not default_token_generator.check_token(user, token)
            ):
                return Response(
                    self.invalid_link_response,
                    status=status.HTTP_400_BAD_REQUEST,
                )

            password_serializer = SetPasswordSerializer(
                data=request.data,
                context={"user": user},
            )
            password_serializer.is_valid(raise_exception=True)
            user.set_password(password_serializer.validated_data["password"])
            user.save(update_fields=["password"])

            outstanding_tokens = OutstandingToken.objects.select_for_update().filter(
                user=user
            )
            for outstanding_token in outstanding_tokens:
                BlacklistedToken.objects.get_or_create(token=outstanding_token)

        return Response(
            {"detail": "Password reset successfully."},
            status=status.HTTP_200_OK,
        )


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = ProfileSerializer
    permission_classes = [CustomerOnly]

    def get_object(self):
        return self.request.user


class AgeVerificationView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]

    def post(self, request):
        serializer = StrictDateOfBirthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submitted_dob = serializer.validated_data.get("date_of_birth")
        declaration_requested = serializer.validated_data.get(
            "age_self_declaration", False
        )

        if submitted_dob is not None and submitted_dob > timezone.localdate():
            raise ValidationError(
                {"date_of_birth": "Date of birth cannot be in the future."}
            )

        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            has_approved_proof = has_approved_age_proof(user)
            if submitted_dob is not None and not has_approved_proof:
                dob_changed = user.date_of_birth != submitted_dob
                user.date_of_birth = submitted_dob
                user.age_verified = False
                user.age_verified_at = None
                if dob_changed:
                    user.age_self_declared = False
                    user.age_self_declared_at = None
                user.save(
                    update_fields=[
                        "date_of_birth",
                        "age_verified",
                        "age_verified_at",
                        "age_self_declared",
                        "age_self_declared_at",
                    ]
                )
            effective_dob = user.date_of_birth
            if declaration_requested:
                if not is_at_least_minimum_age(effective_dob):
                    raise ValidationError(
                        {
                            "date_of_birth": (
                                "You must be at least 21 years old to make this declaration."
                            )
                        }
                    )
                if not user.age_self_declared:
                    user.age_self_declared = True
                    user.age_self_declared_at = timezone.now()
                    user.save(
                        update_fields=[
                            "age_self_declared",
                            "age_self_declared_at",
                        ]
                    )

        return Response({
            "date_of_birth": user.date_of_birth,
            "age_verified": is_age_verified(user),
            "age_verified_at": (
                user.age_verified_at if is_age_verified(user) else None
            ),
            "age_self_declared": is_age_self_declared(user),
            "age_self_declared_at": (
                user.age_self_declared_at if is_age_self_declared(user) else None
            ),
            "age_verification_status": age_proof_status(user),
        })


def _google_login_error(status_code=status.HTTP_401_UNAUTHORIZED):
    return Response(
        {"detail": "Unable to authenticate with Google."},
        status=status_code,
    )


def _google_user_is_eligible(user):
    # A pending shop applicant can authenticate to permitted onboarding APIs;
    # operational shop APIs continue to enforce IsApprovedActiveShopAdmin.
    return (
        user.is_active
        and not user.is_blocked
        and user.user_type in {"customer", "shop_admin", "super_admin"}
    )


def _google_token_response(user):
    refresh = RefreshToken.for_user(user)
    return Response(
        {
            "id": user.id,
            "email": user.email,
            "phone": user.phone,
            "user_type": user.user_type,
            "is_verified": user.is_verified,
            "age_verified": is_age_verified(user),
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        },
        status=status.HTTP_200_OK,
    )


class GoogleLoginView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google_login"

    def post(self, request):
        serializer = GoogleIdTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            identity = verify_google_id_token(serializer.validated_data["id_token"])
        except InvalidGoogleIdentity as exc:
            logger.warning(
                "Google login rejected. reason=%s",
                exc.reason_code,
            )
            return _google_login_error()
        except GoogleVerificationUnavailable:
            return _google_login_error(status.HTTP_503_SERVICE_UNAVAILABLE)

        user = None
        try:
            with transaction.atomic():
                user = User.objects.select_for_update().filter(
                    google_sub=identity.subject
                ).first()
                if user is None:
                    email_matches = list(
                        User.objects.select_for_update().filter(
                            email__iexact=identity.email
                        )
                    )
                    if email_matches:
                        if (
                            len(email_matches) != 1
                            or email_matches[0].google_sub != identity.subject
                        ):
                            logger.warning(
                                "Google login rejected. "
                                "reason=unlinked_email_collision"
                            )
                            return _google_login_error()
                        user = email_matches[0]
                    else:
                        user = User(
                            username=f"google_{uuid.uuid4().hex}",
                            email=identity.email,
                            email_verified=True,
                            google_sub=identity.subject,
                            auth_provider="google",
                            user_type="customer",
                            is_active=True,
                        )
                        user.set_unusable_password()
                        user.save(force_insert=True)

                if not _google_user_is_eligible(user):
                    logger.warning(
                        "Google login rejected. reason=account_ineligible "
                        "active=%s blocked=%s supported_role=%s",
                        bool(user.is_active),
                        bool(user.is_blocked),
                        user.user_type
                        in {"customer", "shop_admin", "super_admin"},
                    )
                    return _google_login_error()
        except IntegrityError:
            # Concurrent first login may have inserted this exact subject.
            # Resolve only by the unique Google subject, never by email.
            try:
                with transaction.atomic():
                    user = User.objects.select_for_update().filter(
                        google_sub=identity.subject
                    ).first()
            except DatabaseError:
                return _google_login_error(status.HTTP_503_SERVICE_UNAVAILABLE)
            if user is None:
                logger.warning(
                    "Google login rejected. reason=unresolved_subject_race"
                )
                return _google_login_error()
            if not _google_user_is_eligible(user):
                logger.warning(
                    "Google login rejected. reason=account_ineligible "
                    "active=%s blocked=%s supported_role=%s",
                    bool(user.is_active),
                    bool(user.is_blocked),
                    user.user_type in {"customer", "shop_admin", "super_admin"},
                )
                return _google_login_error()
        except DatabaseError:
            return _google_login_error(status.HTTP_503_SERVICE_UNAVAILABLE)

        return _google_token_response(user)


class GoogleLinkView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google_link"

    def post(self, request):
        serializer = GoogleIdTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            identity = verify_google_id_token(
                serializer.validated_data["id_token"],
                require_fresh=True,
            )
        except InvalidGoogleIdentity:
            return Response(
                {"detail": "Unable to link Google account."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except GoogleVerificationUnavailable:
            return Response(
                {"detail": "Google verification is temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                if not _google_user_is_eligible(user):
                    return Response(
                        {"detail": "Unable to link Google account."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

                if user.google_sub:
                    if user.google_sub == identity.subject:
                        return Response(
                            {"detail": "Google account is already linked."},
                            status=status.HTTP_200_OK,
                        )
                    return Response(
                        {"detail": "Unable to link Google account."},
                        status=status.HTTP_409_CONFLICT,
                    )

                if User.objects.filter(google_sub=identity.subject).exists():
                    return Response(
                        {"detail": "Unable to link Google account."},
                        status=status.HTTP_409_CONFLICT,
                    )

                user.google_sub = identity.subject
                user.save(update_fields=["google_sub"])
        except IntegrityError:
            # The unique database constraint is the final guard against two
            # users linking the same subject at the same time.
            return Response(
                {"detail": "Unable to link Google account."},
                status=status.HTTP_409_CONFLICT,
            )
        except DatabaseError:
            return Response(
                {"detail": "Google account linking is temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        logger.info(
            "Google account linked. user_id=%s user_type=%s",
            request.user.pk,
            request.user.user_type,
        )
        return Response(
            {"detail": "Google account linked successfully."},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not isinstance(request.data, dict):
            raise ValidationError({"refresh": "This field is required."})
        raw_refresh = request.data.get("refresh")
        if not isinstance(raw_refresh, str) or not raw_refresh:
            raise ValidationError({"refresh": "This field is required."})

        try:
            refresh = RefreshToken(raw_refresh)
        except TokenError as exc:
            raise ValidationError(
                {"refresh": "Invalid or expired refresh token."}
            ) from exc

        token_user_id = refresh.get(api_settings.USER_ID_CLAIM)
        if str(token_user_id) != str(request.user.pk):
            return Response(
                {"detail": "Refresh token does not belong to this user."},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh.blacklist()
        return Response(status=status.HTTP_205_RESET_CONTENT)
class MeView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        return Response({
            "id": user.id,
            "firebase_uid": user.firebase_uid,
            "email": user.email,
            "phone": user.phone,
            "user_type": user.user_type,
            "is_verified": user.is_verified,
            "age_verified": is_age_verified(user),
            "age_self_declared": is_age_self_declared(user),
            "age_self_declared_at": (
                user.age_self_declared_at if is_age_self_declared(user) else None
            ),
            "date_of_birth": user.date_of_birth,
            "age_verified_at": (
                user.age_verified_at if is_age_verified(user) else None
            ),
        })
