# Generated by Django 4.2.17 on 2025-01-22 11:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidates", "0015_candidatealert_created_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidatealert",
            name="filename",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
