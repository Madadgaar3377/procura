"""Shared identity validation. No network lookups or account mutations."""

import re


def normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 200 or value.count("@") != 1:
        raise ValueError("Invalid email address")
    local, domain = value.split("@")
    if not re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}", local):
        raise ValueError("Invalid email address")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("Invalid email address")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid email address") from exc
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    ):
        raise ValueError("Invalid email address")
    if len(labels[-1]) < 2 or labels[-1].isdigit():
        raise ValueError("Invalid email address")
    return f"{local}@{domain}"


def normalize_phone(value: str, country: str | None = None, *, sms: bool = False) -> str:
    """Parse metadata-defined national rules; never infer a region from local digits.

    Country is an ISO region, not a calling code. A pasted explicit international
    number is authoritative; reject country mismatch only when region is known.
    Formatting and metadata validation do not prove ownership or SMS delivery.
    """
    import phonenumbers as pn

    if country is not None:
        country = country.upper().strip()
        if country not in pn.SUPPORTED_REGIONS:
            raise ValueError("Select a supported country or territory.")
    value = value.strip()
    if not value.startswith("+") and country is None:
        raise ValueError("Select a country for a national phone number, or include + and the calling code.")
    if not value or len(value) > 80 or re.search(r"[A-Za-z#;]", value):
        raise ValueError("Invalid phone number. Extensions are not supported.")
    try:
        number = pn.parse(value, country)
    except pn.NumberParseException as exc:
        raise ValueError("Invalid phone number.") from exc
    if number.extension or not pn.is_possible_number(number) or not pn.is_valid_number(number):
        raise ValueError("Invalid phone number for the selected country.")
    if country and not pn.is_valid_number_for_region(number, country):
        raise ValueError("Phone number does not match the selected country.")
    if sms and pn.number_type(number) not in (pn.PhoneNumberType.MOBILE, pn.PhoneNumberType.FIXED_LINE_OR_MOBILE):
        raise ValueError("Use a mobile number capable of receiving SMS verification codes.")
    return pn.format_number(number, pn.PhoneNumberFormat.E164)


def normalize_username(value: str) -> str:
    value = value.strip().casefold()
    if not re.fullmatch(r"[a-z0-9_][a-z0-9_.-]{2,79}", value):
        raise ValueError(
            "Username must contain 3–80 letters, digits, dots, hyphens or underscores; @ is not allowed."
        )
    return value


def validate_password(value: str) -> str:
    if not 6 <= len(value) <= 128 or value != value.strip():
        raise ValueError("Password must contain 6-128 characters without leading or trailing spaces.")
    return value


def normalize_saudi_phone(value: str, country: str | None = None, *, sms: bool = True) -> str:
    """Authentication identity policy; global parsing remains for business/legacy data."""
    import phonenumbers as pn
    if country and country.strip().upper() != 'SA':
        raise ValueError('Only Saudi Arabian mobile numbers are supported. Legacy accounts: use email login or contact support.')
    canonical = normalize_phone(value, 'SA', sms=True)
    number = pn.parse(canonical, 'SA')
    if number.country_code != 966 or not pn.is_valid_number_for_region(number, 'SA'):
        raise ValueError('Use a valid Saudi Arabian mobile number (+966).')
    return canonical
