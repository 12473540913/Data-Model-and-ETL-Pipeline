import json
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def gwas_saved_records():
    path = Path(__file__).resolve().parents[1] / "case-studies" / "rs4988235-associations.json"
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
