from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.core.mail import send_mail
from django.template import loader


class VerifiedEmailPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        for user in super().get_users(email):
            # Delivery of the reset message itself proves control of the
            # address.  Do not exclude password accounts created before an
            # email-verification feature existed; Google/OTP-only accounts
            # already have unusable passwords and are excluded by Django.
            if user.has_usable_password():
                yield user

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        subject = "".join(
            loader.render_to_string(subject_template_name, context).splitlines()
        )
        message = loader.render_to_string(email_template_name, context)
        send_mail(
            subject,
            message,
            from_email or settings.DEFAULT_FROM_EMAIL,
            [to_email],
            fail_silently=True,
        )
