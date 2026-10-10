import json
from decimal import Decimal

import pytest

from genomic_platform.etl.loading import numeric_supported
from genomic_platform.etl.raw_store import encode_raw_json


def test_raw_encoding_preserves_decimal_tokens_and_deterministic_order():
    payload = {"z": [Decimal("3e-1451"), None, True], "a": Decimal("1.234567890123456789")}
    encoded = encode_raw_json(payload)
    assert encoded == encode_raw_json(dict(reversed(list(payload.items()))))
    assert json.loads(encoded, parse_float=Decimal) == payload
    assert "3E-1451" in encoded
    assert '"3E-1451"' not in encoded


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), float("inf")])
def test_raw_encoding_rejects_non_json_numbers(value):
    with pytest.raises(ValueError):
        encode_raw_json({"value": value})


@pytest.mark.parametrize(
    "value, supported",
    [
        ("3e-1451", True),
        ("1e-16383", True),
        ("1e-16384", False),
        ("1e131071", True),
        ("1e131072", False),
        ("NaN", False),
    ],
)
def test_postgresql_numeric_boundaries(value, supported):
    assert numeric_supported(Decimal(value)) is supported
