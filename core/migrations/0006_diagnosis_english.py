import csv
from pathlib import Path

from django.db import migrations

BUNDLED = Path(__file__).resolve().parent.parent / 'data' / 'icd10_umum.csv'

# Indonesian names the bundled list used briefly before its final wording.
EARLIER_NAMES = {'E78.5': ['Hiperlipidemia']}


def to_english(apps, schema_editor):
    """Diagnoses saved from the old Indonesian list get the English WHO title of the same code.
    Only exact matches of a bundled Indonesian name are touched: free-text diagnoses and names
    typed, edited or imported differently keep exactly what was saved."""
    Diagnosis = apps.get_model('core', 'Diagnosis')
    if not BUNDLED.exists():
        return
    with BUNDLED.open(encoding='utf-8-sig', newline='') as fh:
        for row in csv.DictReader(fh):
            old_names = [row['alias'], *EARLIER_NAMES.get(row['code'], [])] if row.get('alias') else []
            if old_names:
                Diagnosis.objects.filter(code=row['code'], name__in=old_names).update(name=row['name'])


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0005_icd10_english'),
    ]

    operations = [
        migrations.RunPython(to_english, migrations.RunPython.noop),
    ]
