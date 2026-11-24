import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Icd10

DATA = Path(__file__).resolve().parents[2] / 'data'


class Command(BaseCommand):
    # load_icd9 reuses this command for the ICD-9-CM procedure list.
    model = Icd10
    label = 'ICD-10'
    bundled = DATA / 'icd10_umum.csv'

    help = (
        'Imports ICD-10 codes from a CSV with columns: code, name (the WHO title, English) and optionally '
        'alias (extra search words, e.g. Indonesian terms; never displayed). Header row optional; comma, '
        'semicolon or tab separated. Without a path, loads the bundled list of common codes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', help='CSV file to import')
        parser.add_argument('--if-empty', action='store_true', help='Only import when the table is empty')
        parser.add_argument('--replace', action='store_true', help='Delete all existing codes first')

    def handle(self, *args, path=None, if_empty=False, replace=False, **options):
        if if_empty and self.model.objects.exists():
            return
        source = Path(path) if path else self.bundled
        if not source.exists():
            raise CommandError(f'File not found: {source}')

        rows = {}
        with source.open(encoding='utf-8-sig', newline='') as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            for row in csv.reader(fh, dialect):
                if len(row) < 2:
                    continue
                code, name = row[0].strip().upper(), ' '.join(row[1].split())
                if not code or not name or code in ('CODE', 'KODE'):
                    continue
                # None = file has no alias column: keep whatever alias the code already has.
                alias = ' '.join(row[2].split())[:300] if len(row) > 2 else None
                rows[code[:10]] = (name[:300], alias)

        with transaction.atomic():
            if replace:
                self.model.objects.all().delete()
            existing = {c.code: c for c in self.model.objects.filter(code__in=rows)}
            to_update = []
            for code, (name, alias) in rows.items():
                item = existing.get(code)
                if item and (item.name != name or (alias is not None and item.alias != alias)):
                    item.name = name
                    if alias is not None:
                        item.alias = alias
                    to_update.append(item)
            self.model.objects.bulk_update(to_update, ['name', 'alias'], batch_size=1000)
            self.model.objects.bulk_create(
                [self.model(code=c, name=n, alias=a or '') for c, (n, a) in rows.items() if c not in existing],
                batch_size=1000,
            )
        self.stdout.write(self.style.SUCCESS(f'{self.label}: {len(rows)} codes read from {source.name}.'))
