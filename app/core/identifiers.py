"""Formats shared by every request that carries a phone number or a Colombian NIT.

Values are normalized before they are stored, so the same number always reads the same way: the separators
people usually type (spaces, dots, dashes, parentheses) are dropped from phones, and a NIT keeps only its
digits and the dash before the check digit.
"""
import re
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator

PHONE_PATTERN = re.compile(r"\+?\d{7,15}")
PHONE_SEPARATORS = re.compile(r"[\s().-]")

NIT_PATTERN = re.compile(r"(\d{6,10})-(\d)")
NIT_SEPARATORS = re.compile(r"[\s.]")
# DIAN weights for the check digit, applied from the rightmost digit of the number.
NIT_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43)


def nit_check_digit(number: str) -> int:
    remainder = sum(int(digit) * weight for digit, weight in zip(reversed(number), NIT_WEIGHTS)) % 11
    return remainder if remainder < 2 else 11 - remainder


def validate_phone(value: str) -> str:
    phone = PHONE_SEPARATORS.sub("", value)
    if not PHONE_PATTERN.fullmatch(phone):
        raise ValueError("El teléfono debe tener entre 7 y 15 dígitos, con + opcional al inicio")
    return phone


def validate_nit(value: str) -> str:
    nit = NIT_SEPARATORS.sub("", value)
    match = NIT_PATTERN.fullmatch(nit)
    if match is None:
        raise ValueError("El NIT debe tener el número y el dígito de verificación separados por guion (900123456-8)")
    number, check_digit = match.groups()
    if nit_check_digit(number) != int(check_digit):
        raise ValueError("El dígito de verificación del NIT no corresponde al número")
    return nit


def _blank_to_none(value: object) -> object:
    """Optional fields accept an empty string to mean "no value", as forms send them."""
    return None if isinstance(value, str) and not value.strip() else value


Phone = Annotated[str, AfterValidator(validate_phone)]
Nit = Annotated[str, AfterValidator(validate_nit)]
OptionalPhone = Annotated[Phone | None, BeforeValidator(_blank_to_none)]
OptionalNit = Annotated[Nit | None, BeforeValidator(_blank_to_none)]
