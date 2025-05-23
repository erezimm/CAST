# Generated by Django 4.2.17 on 2025-01-23 12:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidates", "0016_candidatealert_filename"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidatealert",
            name="diff_cutout_filename",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="candidatealert",
            name="new_cutout_filename",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="candidatealert",
            name="ref_cutout_filename",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="candidatedataproduct",
            name="data_product_type",
            field=models.CharField(
                choices=[
                    ("ref", "ref"),
                    ("new", "new"),
                    ("diff", "diff"),
                    ("ps1", "ps1"),
                    ("json", "json"),
                    ("tns_report", "tns_report"),
                ],
                max_length=50,
            ),
        ),
    ]
