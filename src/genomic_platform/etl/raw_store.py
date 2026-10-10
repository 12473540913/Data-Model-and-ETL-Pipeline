import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from genomic_platform.config import get_settings


def encode_raw_json(payload: Any) -> str:
    """Canonical JSON bytes retain Decimal numeric tokens, not quoted approximations."""
    from decimal import Decimal

    if isinstance(payload, Decimal):
        if not payload.is_finite():
            raise ValueError("Raw JSON cannot contain non-finite Decimal values.")
        return str(payload)
    if isinstance(payload, dict):
        if not all(isinstance(key, str) for key in payload):
            raise TypeError("Raw JSON object keys must be strings.")
        return (
            "{"
            + ",".join(
                json.dumps(key) + ":" + encode_raw_json(payload[key]) for key in sorted(payload)
            )
            + "}"
        )
    if isinstance(payload, (list, tuple)):
        return "[" + ",".join(encode_raw_json(value) for value in payload) + "]"
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


def persist_raw_json(
    source: str, record_type: str, record_id: str, payload: Any
) -> tuple[str, str]:
    body = encode_raw_json(payload).encode()
    digest = hashlib.sha256(body).hexdigest()
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in record_id)
    path = get_settings().raw_data_dir / source.lower() / day / record_type
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{safe_id}-{digest[:12]}.json"
    target.write_bytes(body)
    return str(target), digest
