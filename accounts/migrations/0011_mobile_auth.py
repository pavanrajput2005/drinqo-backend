from django.db import migrations, models
from django.db.models.functions import Lower
from django.core.validators import RegexValidator


def prepare_users(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    alias = schema_editor.connection.alias
    seen = set()
    for user in User.objects.using(alias).all().iterator():
        email = user.email.strip().lower()
        if email in seen:
            raise RuntimeError(
                "Case-insensitive duplicate account emails exist. Resolve account ownership before migrating."
            )
        seen.add(email)
    for user in User.objects.using(alias).all().iterator():
        User.objects.using(alias).filter(pk=user.pk).update(
            email=user.email.strip().lower(), birth_date=user.date_of_birth,
            mobile_number=user.phone or None,
            auth_provider="google" if user.google_sub else "email",
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_user_age_self_declaration")]
    operations = [
        migrations.AddField("user", "birth_date", models.DateField(null=True, blank=True)),
        migrations.AddField("user", "mobile_number", models.CharField(max_length=15, null=True, blank=True)),
        migrations.AddField("user", "auth_provider", models.CharField(
            max_length=10, choices=[("google", "Google"), ("apple", "Apple"), ("email", "Email")], default="email",
        )),
        migrations.AddField("user", "is_profile_complete", models.BooleanField(default=False)),
        migrations.AddField("user", "apple_sub", models.CharField(max_length=255, unique=True, null=True, blank=True)),
        migrations.RunPython(prepare_users, migrations.RunPython.noop),
        migrations.AddConstraint("user", models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique")),
        migrations.CreateModel(name="EmailOTP", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("email", models.EmailField(max_length=254, unique=True)),
            ("otp_code", models.CharField(max_length=6, validators=[RegexValidator(r"\A[0-9]{6}\Z")])),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ]),
        migrations.CreateModel(name="EmailAuthState", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("email_digest", models.CharField(max_length=64, unique=True)),
            ("send_count", models.PositiveSmallIntegerField(default=0)),
            ("verify_count", models.PositiveSmallIntegerField(default=0)),
            ("send_window", models.DateTimeField(null=True, blank=True)),
            ("verify_window", models.DateTimeField(null=True, blank=True)),
            ("last_sent_at", models.DateTimeField(null=True, blank=True)),
        ]),
    ]
