"""Contract tests.

These are the anti-corruption tests: if a future change to PPTAgent's DeckSpec,
the knowledge desktop or research_agent violates an assumption the workbench
makes, it should turn a test red here -- not corrupt a citation at runtime.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from workstation_contracts import (
    Artifact,
    ArtifactKind,
    Budget,
    Confidence,
    EventType,
    Fact,
    FactKind,
    FactSet,
    Origin,
    PermissionResource,
    Priority,
    Run,
    RunEvent,
    RunStatus,
    RuntimeKind,
    SkillManifest,
    SourceRef,
    SourceType,
    StepKind,
    StepStatus,
    TaskRequest,
    Usage,
    derive_source_id,
    is_valid_id,
    new_id,
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


# --------------------------------------------------------------------- ids


def test_id_shape_and_sortability():
    a = new_id("run")
    b = new_id("run")
    assert is_valid_id(a, "run") and is_valid_id(b, "run")
    assert a[:17] <= b[:17], "ids must sort by the millisecond prefix (suffix is random)"
    assert not is_valid_id(a, "fact")
    assert not is_valid_id("run_abc")


def test_id_prefix_strictly_increases_across_milliseconds():
    first = new_id("run")
    time.sleep(0.002)
    assert new_id("run") > first


def test_unknown_prefix_rejected():
    with pytest.raises(ValueError):
        new_id("nope")


# --------------------------------------------------------------- source ref


def _source(**kw) -> SourceRef:
    base = dict(
        source_type=SourceType.PAPER,
        uri="primary/papers/2024-attention.pdf",
        title="Attention Is All You Need",
        producer="research.paper",
        origin=Origin.PRIMARY,
        content_hash="a" * 64,
    )
    base.update(kw)
    return SourceRef.mint(**base)


def test_source_id_is_content_addressed():
    a = _source()
    b = _source()
    assert a.source_id == b.source_id, "same uri+hash must dedupe"
    assert a.source_id.startswith("src_")
    assert derive_source_id(a.uri, "a" * 64) == a.source_id
    assert _source(content_hash="b" * 64).source_id != a.source_id


def test_source_ref_is_frozen_and_revisable():
    s = _source()
    with pytest.raises(ValidationError):
        s.title = "changed"
    revised = s.revise(title="changed")
    assert revised.source_id == s.source_id
    assert revised.updated_at >= s.updated_at


def test_locator_page_is_one_based():
    with pytest.raises(ValidationError):
        _source(locator={"page": 0})


def test_extra_fields_rejected():
    """Drop-in guard: adapters must map explicitly, not pass unknown keys through."""
    with pytest.raises(ValidationError):
        SourceRef(
            source_id="src_x",
            source_type=SourceType.PAPER,
            uri="u",
            title="t",
            origin=Origin.PRIMARY,
            producer="p",
            surprise_field=1,
        )


def test_is_local():
    assert _source().is_local
    assert not _source(uri="https://arxiv.org/abs/1706.03762").is_local


# --------------------------------------------------------------------- fact


def test_fact_requires_at_least_one_source():
    with pytest.raises(ValidationError):
        Fact(text="a claim with no provenance")


def test_fact_dedupes_sources():
    f = Fact(text="x", source_ids=["src_a", "src_a", "src_b"])
    assert f.source_ids == ["src_a", "src_b"]


def test_citable_gate():
    high = Fact(text="x", source_ids=["src_a"], confidence=Confidence.HIGH)
    low = Fact(text="y", source_ids=["src_a"], confidence=Confidence.LOW)
    assert high.is_citable and not low.is_citable


def test_factset_density_and_page_recommendation():
    facts = [
        Fact(text="t" * 200, source_ids=["src_a"], confidence=Confidence.HIGH)
        for _ in range(12)
    ]
    fs = FactSet(facts=facts, label="study A")
    assert fs.density() > 0
    assert fs.recommends_page_count(facts_per_page=3.0) == 4
    assert FactSet().recommends_page_count() == 0, "no citable facts -> no deck"


def test_fact_kind_defaults():
    assert Fact(text="x", source_ids=["s"]).kind is FactKind.CLAIM


# ---------------------------------------------------------------------- run


def _req() -> TaskRequest:
    return TaskRequest(skill="research.deep", inputs={"query": "x"})


def test_run_lifecycle():
    run = Run.from_request(_req())
    assert run.status is RunStatus.PENDING and not run.is_terminal
    run.transition(RunStatus.RUNNING)
    assert run.started_at is not None

    step = run.add_step("retrieve", StepKind.RETRIEVE)
    step.usage.llm_calls = 1
    step.usage.prompt_tokens = 500
    step.finish(StepStatus.SUCCEEDED, message="12 hits")
    assert step.duration_s >= 0

    run.recompute_usage()
    assert run.usage.prompt_tokens == 500

    run.transition(RunStatus.SUCCEEDED)
    assert run.is_terminal and run.ended_at is not None
    with pytest.raises(ValueError, match="terminal"):
        run.transition(RunStatus.RUNNING)


def test_budget_breach_is_explicit():
    run = Run.from_request(
        TaskRequest(
            skill="research.deep",
            options={"budget": {"max_cost_cny": 1.0}, "priority": Priority.BATCH},
        )
    )
    run.usage.cost_cny = 2.5
    assert run.budget_breach() == "max_cost_cny"
    assert run.options.priority is Priority.BATCH


def test_budget_within_limits():
    assert Budget(max_tokens=1000).breach(Usage(prompt_tokens=10)) is None


def test_parent_run_id_shape():
    with pytest.raises(ValidationError):
        Run(skill="s", parent_run_id="not-an-id")


def test_idempotency_key_propagates():
    req = TaskRequest(skill="s", options={"idempotency_key": "k1"})
    assert Run.from_request(req).idempotency_key == "k1"


def test_artifact_records_provenance():
    art = Artifact(
        kind=ArtifactKind.DECK,
        uri="primary/decks/kaoti.pptx",
        origin=Origin.PRIMARY,
        producer="pptx.render",
        source_ids=["src_a"],
        fact_ids=["fact_a"],
    )
    assert art.origin is Origin.PRIMARY


# -------------------------------------------------------------------- event


def test_sse_frame_is_single_line_data():
    run = Run.from_request(_req())
    evt = RunEvent(run_id=run.run_id, seq=0, type=EventType.RUN_CREATED)
    frame = evt.to_sse()
    lines = frame.strip().split("\n")
    data_lines = [ln for ln in lines if ln.startswith("data:")]
    assert len(data_lines) == 1, "multi-line data breaks naive SSE parsers"
    payload = json.loads(data_lines[0][len("data:") :])
    assert payload["run_id"] == run.run_id
    assert payload["type"] == "run.created"


def test_event_rejects_bad_run_id():
    with pytest.raises(ValidationError):
        RunEvent(run_id="oops", seq=0, type=EventType.RUN_CREATED)


# -------------------------------------------------------------------- skill


def test_manifest_name_pattern():
    with pytest.raises(ValidationError):
        SkillManifest(name="Bad_Name", description="d", runtime={"kind": "inprocess", "entrypoint": "m"})


def test_manifest_permissions():
    m = SkillManifest(
        name="pptx",
        description="render decks",
        runtime={"kind": RuntimeKind.SUBPROCESS, "entrypoint": "node cli.js", "isolated": True},
        permissions=[{"resource": "fs:primary", "requires_confirm": True}],
    )
    assert m.runtime.isolated, "pptxgenjs module state forces process isolation"
    assert m.writes_primary()
    assert m.needs_confirmation(PermissionResource.FS_PRIMARY)
    assert not m.needs_confirmation(PermissionResource.LLM)


# ------------------------------------------------------------------- schema


@pytest.mark.parametrize("name", ["source-ref", "fact", "run", "task-request", "skill-manifest"])
def test_generated_schema_is_present_and_non_empty(name: str):
    path = SCHEMA_DIR / f"{name}.schema.json"
    assert path.exists(), f"run scripts/gen_schema.py -- {path} missing"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc.get("properties"), name
