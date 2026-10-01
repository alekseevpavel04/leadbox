import pytest

from leadbox.bot.contact import normalize_contact, shared_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (999) 123-45-67", "+79991234567"),
        ("+7 999 1234567", "+79991234567"),
        ("79991234567", "+79991234567"),
        ("9991234567", "+79991234567"),
        ("+380 50 123 45 67", "+380501234567"),
        ("+44 20 7946 0958", "+442079460958"),
        ("  8-999-123-45-67  ", "+79991234567"),
        ("@user", "@user"),
        ("@Anna_Tg", "@Anna_Tg"),
        ("t.me/anna_tg", "@anna_tg"),
        ("https://t.me/anna_tg", "@anna_tg"),
        ("a@b.c", "a@b.c"),
        ("name.surname@mail.ru", "name.surname@mail.ru"),
    ],
)
def test_contact_is_recognised(raw, expected):
    assert normalize_contact(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "12345",
        "abc",
        "",
        "@ab",
        "@1user",
        "+7 999 12",
        "+1234567890123456",
        "8 999 123 45 67 доб. 12",
        "a@b",
        "мой номер 89991234567",
    ],
)
def test_contact_is_rejected(raw):
    assert normalize_contact(raw) is None


def test_shared_phone_gets_a_plus():
    assert shared_phone("79991234567") == "+79991234567"
    assert shared_phone("+79991234567") == "+79991234567"
