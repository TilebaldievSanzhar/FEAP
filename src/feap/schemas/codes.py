"""Canonical numeric tariff codes at the model boundary.

Accept display separators from JSON-mode providers, but never extract a code
from prose, truncate digits, or pad a different code with zeroes.
"""
from typing import Annotated

from pydantic import BeforeValidator, Field


def normalize_code(value: object) -> object:
    if isinstance(value, str):
        return ''.join(c for c in value if not c.isspace() and c != '.')
    return value


Code2 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^[0-9]{2}$')]
Code4 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^[0-9]{4}$')]
Code6 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^[0-9]{6}$')]
Code8 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^[0-9]{8}$')]
Code10 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^[0-9]{10}$')]
# A blank decision is only meaningful with a backtrack or missing parameters.
Decision2 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^(?:[0-9]{2})?$')]
Decision4 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^(?:[0-9]{4})?$')]
Decision6 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^(?:[0-9]{6})?$')]
Decision8 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^(?:[0-9]{8})?$')]
Decision10 = Annotated[str, BeforeValidator(normalize_code), Field(pattern=r'^(?:[0-9]{10})?$')]
