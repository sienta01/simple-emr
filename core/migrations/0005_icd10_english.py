import csv
from pathlib import Path

from django.db import migrations, models

BUNDLED = Path(__file__).resolve().parent.parent / 'data' / 'icd10_umum.csv'


def to_english(apps, schema_editor):
    """Existing installs loaded the bundled list in Indonesian. Switch those rows to the English WHO
    title and keep the Indonesian name as a search alias. Rows whose name was changed or imported
    by the clinic do not match the old Indonesian name and are left alone."""
    Icd10 = apps.get_model('core', 'Icd10')
    if not BUNDLED.exists():
        return
    with BUNDLED.open(encoding='utf-8-sig', newline='') as fh:
        for row in csv.DictReader(fh):
            if row.get('alias'):
                Icd10.objects.filter(code=row['code'], name=row['alias']).update(name=row['name'], alias=row['alias'])


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0004_import_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='icd10',
            name='alias',
            field=models.CharField(blank=True, max_length=300, verbose_name='kata kunci'),
        ),
        migrations.AlterField(
            model_name='icd10',
            name='name',
            field=models.CharField(help_text='Judul resmi WHO (bahasa Inggris).', max_length=300, verbose_name='nama'),
        ),
        migrations.RunPython(to_english, migrations.RunPython.noop),
    ]
