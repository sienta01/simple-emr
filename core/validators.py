import re

from django.core.exceptions import ValidationError


class CharacterClassValidator:
    """Requires certain kinds of characters in a password. Switched on per rule from .env (PASSWORD_REQUIRE_*)."""

    RULES = {
        'digit': (re.compile(r'\d'), 'satu angka'),
        'upper': (re.compile(r'[A-Z]'), 'satu huruf besar'),
        'symbol': (re.compile(r'[^A-Za-z0-9]'), 'satu simbol (mis. ! @ # -)'),
    }

    def __init__(self, require_digit=False, require_upper=False, require_symbol=False):
        wanted = {'digit': require_digit, 'upper': require_upper, 'symbol': require_symbol}
        self.required = [key for key, on in wanted.items() if on]

    def _describe(self, keys):
        return 'Kata sandi harus mengandung minimal ' + ', '.join(self.RULES[k][1] for k in keys) + '.'

    def validate(self, password, user=None):
        missing = [key for key in self.required if not self.RULES[key][0].search(password)]
        if missing:
            raise ValidationError(self._describe(missing), code='password_missing_character_class')

    def get_help_text(self):
        return self._describe(self.required) if self.required else ''
