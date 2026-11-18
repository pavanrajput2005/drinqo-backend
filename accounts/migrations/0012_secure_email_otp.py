from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def invalidate_plaintext(apps, schema_editor):
    # Old email OTPs are ephemeral proofs. Invalidate and clear their plaintext;
    # do not touch users, phone identities or historical order evidence.
    apps.get_model("accounts", "EmailOTP").objects.using(schema_editor.connection.alias).update(otp_code="")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0011_mobile_auth")]
    operations = [
        migrations.AlterField("emailotp", "otp_code", models.CharField(max_length=64)),
        migrations.AddField("emailotp", "purpose", models.CharField(max_length=10, default="login", choices=[("login", "Login"), ("link", "Link email")])),
        migrations.AddField("emailotp", "requesting_user", models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.CASCADE)),
        migrations.RunPython(invalidate_plaintext, migrations.RunPython.noop),
    ]
