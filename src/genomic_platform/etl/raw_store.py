import hashlib, json
from datetime import datetime, timezone
from typing import Any
from genomic_platform.config import get_settings

def persist_raw_json(source: str, record_type: str, record_id: str, payload: Any) -> tuple[str, str]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha256(body).hexdigest(); day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in record_id)
    path = get_settings().raw_data_dir / source.lower() / day / record_type; path.mkdir(parents=True, exist_ok=True)
    target = path / f"{safe_id}-{digest[:12]}.json"; target.write_bytes(body)
    return str(target), digest
