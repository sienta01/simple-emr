from core.models import Icd9

from .load_icd10 import DATA, Command as LoadIcd10


class Command(LoadIcd10):
    model = Icd9
    label = 'ICD-9-CM'
    bundled = DATA / 'icd9cm_umum.csv'

    help = (
        'Imports ICD-9-CM procedure codes (Tindakan) from a CSV with columns: code (e.g. 86.59), name '
        '(the official title, English) and optionally alias (extra search words, e.g. Indonesian terms; '
        'never displayed). Header row optional; comma, semicolon or tab separated. Without a path, loads '
        'the bundled list of common primary-care procedures.'
    )
