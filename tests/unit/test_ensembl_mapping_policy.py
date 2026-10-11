import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from genomic_platform.connectors.ensembl import ensembl_retry_wait
from genomic_platform.connectors.http import ResilientHttpClient
from genomic_platform.etl.ensembl_normalization import forward_snp_alleles, normalize_ensembl
from genomic_platform.etl.normalize import ensembl_to_variant


@pytest.fixture
def payload():
    return json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "ensembl-rs4988235.json").read_text()
    )


@pytest.mark.parametrize("assembly", ["GRCh38", "GRCh37"])
def test_no_fabricated_assembly_or_arbitrary_mapping(payload, assembly):
    payload["mappings"] = [
        {**payload["mappings"][0], "assembly_name": assembly},
        {**payload["mappings"][0], "assembly_name": assembly, "seq_region_name": "X"},
    ]
    dto = ensembl_to_variant("rs4988235", payload, assembly)
    assert dto.assembly is dto.chromosome is dto.position is None
    assert len(dto.metadata["all_mappings"]) == 2
    payload["mappings"] = [{}]
    dto = ensembl_to_variant("rs4988235", payload, assembly)
    assert dto.assembly is dto.chromosome is dto.position is None


@pytest.mark.parametrize(
    "change",
    [
        {"strand": -1},
        {"strand": None},
        {"allele_string": "G/-"},
        {"allele_string": "G/AT"},
        {"allele_string": "N/A"},
        {"end": 135851077},
    ],
)
def test_complex_or_reverse_alleles_are_not_invented(payload, change):
    payload["mappings"][0].update(change)
    result = normalize_ensembl(payload, "rs4988235", "GRCh38")
    assert forward_snp_alleles(result.mappings[0]) is None
    dto = ensembl_to_variant("rs4988235", payload)
    assert dto.reference_allele is None and dto.alternate_alleles == []


@pytest.mark.parametrize("position", [0, -1, True, 2**63, "135851076", 1.5])
def test_invalid_mapping_coordinate_remains_unprojected(payload, position):
    payload["mappings"][0]["start"] = position
    assert not normalize_ensembl(payload, "rs4988235", "GRCh38").candidates


@pytest.mark.parametrize("header", ["2", "nonsense", "-1", "nan"])
def test_retry_after_is_honored_or_explicitly_rejected(header):
    request = httpx.Request("GET", "https://rest.ensembl.org/info/ping")
    response = httpx.Response(429, headers={"Retry-After": header}, request=request)
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)
    state = SimpleNamespace(attempt_number=1, outcome=SimpleNamespace(exception=lambda: error))
    delay = ensembl_retry_wait(state)
    assert delay == (2 if header == "2" else 0.5)


def test_retry_after_date_and_excessive_cooldown():
    request = httpx.Request("GET", "https://rest.ensembl.org/info/ping")
    response = httpx.Response(
        429,
        request=request,
        headers={
            "Retry-After": format_datetime(datetime.now(UTC) + timedelta(seconds=10), usegmt=True)
        },
    )
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)
    state = SimpleNamespace(attempt_number=1, outcome=SimpleNamespace(exception=lambda: error))
    assert 8 <= ensembl_retry_wait(state) <= 10
    response.headers["Retry-After"] = "301"
    with pytest.raises(httpx.HTTPStatusError):
        ensembl_retry_wait(state)


def test_request_rate_pacing(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("genomic_platform.connectors.http.asyncio.sleep", sleep)
    monkeypatch.setattr("genomic_platform.connectors.http.time.monotonic", lambda: 100)
    client = ResilientHttpClient("https://rest.ensembl.org", request_interval=0.2)

    async def exercise():
        await client._pace()
        await client._pace()
        await client.client.aclose()

    asyncio.run(exercise())
    assert sleeps == [0.2]
