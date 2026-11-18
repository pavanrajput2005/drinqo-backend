from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shop_admin", "0008_platformsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformsettings",
            name="nearby_shop_radius_km",
            field=models.DecimalField(
                decimal_places=2,
                default="10.00",
                max_digits=5,
            ),
        ),
    ]
