"""Dump the Pydantic contracts to JSON Schema (draft 2020-12).

Run from the repo root::

    python scripts/gen_schema.py

The output in ``contracts/schema/`` is committed. CI fails if a regeneration
produces a diff, which is what keeps the TypeScript mirror from drifting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "contracts" / "schema"

sys.path.insert(0, str(REPO_ROOT / "contracts" / "python"))

from workstation_contracts import (  # noqa: E402
    CONTRACT_VERSION,
    Artifact,
    Fact,
    FactSet,
    Run,
    RunEvent,
    SkillManifest,
    SourceRef,
    TaskRequest,
)

MODELS = {
    "source-ref": SourceRef,
    "fact": Fact,
    "fact-set": FactSet,
    "run": Run,
    "run-event": RunEvent,
    "task-request": TaskRequest,
    "artifact": Artifact,
    "skill-manifest": SkillManifest,
}


def main() -> int:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        schema = model.model_json_schema(
            ref_template="#/$defs/{model}", mode="serialization", by_alias=True
        )
        schema["$id"] = f"https://workstation.local/schema/{name}.schema.json"
        schema["title"] = model.__name__
        schema["x-contract-version"] = CONTRACT_VERSION
        path = SCHEMA_DIR / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    print(f"\n{len(MODELS)} schemas, contract version {CONTRACT_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
