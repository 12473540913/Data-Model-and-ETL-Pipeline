import json
from decimal import Decimal
from typing import Any


class SourcePayloadError(ValueError):
    pass


def reject_constant(value: str):
    raise SourcePayloadError("Source JSON contains a non-finite numeric token.")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourcePayloadError("Source JSON contains duplicate object keys.")
        result[key] = value
    return result


def decode_source_json(body: str) -> Any:
    return json.loads(
        body, parse_float=Decimal, parse_constant=reject_constant, object_pairs_hook=unique_object
    )


def association_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        candidates = [payload[key] for key in ("content", "results") if key in payload]
        if not candidates and isinstance(payload.get("_embedded"), dict):
            candidates = [
                value for value in payload["_embedded"].values() if isinstance(value, list)
            ]
        if len(candidates) != 1 or not isinstance(candidates[0], list):
            raise SourcePayloadError("Expected one GWAS association collection.")
        records = candidates[0]
    else:
        raise SourcePayloadError("Expected a GWAS association array or collection envelope.")
    if not all(isinstance(record, dict) for record in records):
        raise SourcePayloadError("Every GWAS association must be a JSON object.")
    return records
