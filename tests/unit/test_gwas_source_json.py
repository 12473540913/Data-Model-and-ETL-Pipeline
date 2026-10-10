from decimal import Decimal

import pytest
from typer.testing import CliRunner

from genomic_platform.cli import app
from genomic_platform.etl.ingestion import iter_file_records
from genomic_platform.etl.source_json import (
    SourcePayloadError,
    association_records,
    decode_source_json,
)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"content": []},
        {"results": []},
        {"_embedded": {"associations": []}},
    ],
)
def test_empty_collections_are_valid(payload):
    assert association_records(payload) == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": {}},
        {"results": None},
        {"content": [], "results": []},
        {"_embedded": {"one": [], "two": []}},
        [None],
        [1],
        "invalid",
    ],
)
def test_malformed_collections_are_not_empty_success(payload):
    with pytest.raises(SourcePayloadError):
        association_records(payload)


def test_decode_preserves_extreme_decimal_tokens():
    assert decode_source_json('{"p_value":3e-1451}')["p_value"] == Decimal("3e-1451") > 0


@pytest.mark.parametrize("body", ['{"x":1,"x":2}', '{"p":NaN}', '{"p":Infinity}'])
def test_invalid_json_tokens_are_explicit(body):
    with pytest.raises(SourcePayloadError):
        decode_source_json(body)


@pytest.mark.parametrize("body", ["[]", " [ {} ] ", '[{"p":3e-1451},{}]'])
def test_streamed_file_matches_exact_decoder(tmp_path, body):
    path = tmp_path / "records.json"
    path.write_text(body, encoding="utf-8")
    assert list(iter_file_records(path)) == decode_source_json(body)


@pytest.mark.parametrize(
    "body", ["", "{}", "[", "[{},]", "[null]", "[{}] extra", "[{} {}]", '[{"x":1,"x":2}]']
)
def test_bad_files_fail_without_silent_success(tmp_path, body):
    path = tmp_path / "records.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(SourcePayloadError):
        list(iter_file_records(path))


def test_file_record_crosses_read_chunk_boundary(tmp_path):
    path = tmp_path / "records.json"
    path.write_text('[{"source_field":"' + "x" * 200000 + '"},{"id":2}]', encoding="utf-8")
    records = list(iter_file_records(path))
    assert len(records[0]["source_field"]) == 200000
    assert records[1]["id"] == 2


@pytest.mark.parametrize(
    "command",
    ["ingest-gwas-trait", "ingest-gwas-variant", "ingest-gwas-file", "replay-gwas", "resume-gwas"],
)
def test_new_cli_help(command):
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output


def test_discovery_cli_help_remains_available():
    assert CliRunner().invoke(app, ["discover-traits", "--help"]).exit_code == 0
