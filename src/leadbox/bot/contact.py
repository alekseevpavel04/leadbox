import re

_PHONE_CHARS = re.compile(r"\+?[\d\s()\-.]+")
_USERNAME = re.compile(r"(?:@|(?:https?://)?t\.me/)([A-Za-z][A-Za-z0-9_]{3,31})")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
EMAIL_MAX_LEN = 254


def normalize_phone(raw: str) -> str | None:
    """`+<country code><number>`, 10-15 digits, or None.

    Without a plus the number is taken as Russian: the leads come from Russian ads, and people
    write `8 (999) ...` or drop the country code altogether far more often than they mean a foreign
    number without `+`.
    """
    text = raw.strip()
    if not _PHONE_CHARS.fullmatch(text):
        return None
    digits = re.sub(r"\D", "", text)
    if not text.startswith("+"):
        if len(digits) == 11 and digits[0] in "78":
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


def shared_phone(phone_number: str) -> str:
    # Telegram sends the phone of a shared contact with the country code but sometimes without "+".
    return "+" + re.sub(r"\D", "", phone_number)


def normalize_contact(raw: str) -> str | None:
    """Phone as `+7...`, Telegram `@username` or email; None if it is none of them."""
    text = raw.strip()
    if username := _USERNAME.fullmatch(text):
        return "@" + username.group(1)
    if _EMAIL.fullmatch(text):
        return text if len(text) <= EMAIL_MAX_LEN else None
    return normalize_phone(text)
