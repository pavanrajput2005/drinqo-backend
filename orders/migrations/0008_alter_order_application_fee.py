from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0007_orderlivelocation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="application_fee",
            field=models.DecimalField(
                decimal_places=2,
                default="50.00",
                max_digits=10,
            ),
        ),
    ]
