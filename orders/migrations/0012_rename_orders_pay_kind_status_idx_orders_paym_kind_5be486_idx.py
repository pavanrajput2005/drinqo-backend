# Generated to keep the migration state aligned with PaymentOperationalCase.Meta.
# This renames an existing index only; it does not alter application data.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0011_payment_operations"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="paymentoperationalcase",
            old_name="orders_pay_kind_status_idx",
            new_name="orders_paym_kind_5be486_idx",
        ),
    ]
