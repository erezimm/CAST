# Generated by Django 4.2.17 on 2025-01-26 12:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidates", "0018_alter_candidatedataproduct_data_product_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidate",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
