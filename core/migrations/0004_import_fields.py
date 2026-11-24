from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0003_visit_called_updated'),
    ]

    operations = [
        migrations.AddField(
            model_name='visit',
            name='legacy_id',
            field=models.CharField(blank=True, editable=False, max_length=100, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='patient',
            name='tanggal_lahir',
            field=models.DateField(null=True, verbose_name='tanggal lahir'),
        ),
    ]
