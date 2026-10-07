"""Refresh the bundled offline snapshot from deprecations.info.

Only the fields llm-sunset needs are kept, to keep the package small.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_sunset.data import _download  # noqa: E402

KEEP = ("provider", "model_id", "shutdown_date", "deprecation_date", "replacement_models", "url", "last_observed")
OUT = Path(__file__).resolve().parents[1] / "src" / "llm_sunset" / "snapshot.json"


def main() -> int:
    data = _download(timeout=30)
    if not data:
        print("could not download feed", file=sys.stderr)
        return 1
    slim = [{k: r.get(k) for k in KEEP} for r in data if r.get("model_id")]
    slim.sort(key=lambda r: (r["provider"] or "", r["model_id"]))
    OUT.write_text(json.dumps(slim, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(slim)} records to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
