"""Stage-0 smoke: one paper -> facts -> a deck page-count precheck.

    python examples/stage0_smoke.py

This is the smallest path that exercises every contract at once:
SourceRef (provenance) -> Fact/FactSet (evidence) -> Run (execution)
-> Artifact (output) -> RunEvent (SSE) -> SkillManifest (declaration).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contracts" / "python"))

from workstation_contracts import (  # noqa: E402
    Artifact,
    ArtifactKind,
    Budget,
    Confidence,
    EventType,
    Fact,
    FactKind,
    FactSet,
    Origin,
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
)


def main() -> int:
    # 1. provenance -------------------------------------------------------
    paper = SourceRef.mint(
        source_type=SourceType.PAPER,
        uri="primary/papers/2024-survey-rag.pdf",
        title="A Survey of Retrieval-Augmented Generation",
        producer="research.paper",
        origin=Origin.PRIMARY,
        content_hash="c" * 64,
        locator={"page": 3},
        language="en",
    )
    print(f"[source] {paper.source_id}  local={paper.is_local}  p{paper.locator.page}")

    # 2. evidence ---------------------------------------------------------
    facts = [
        Fact(
            text="RAG reduces hallucination on knowledge-intensive tasks",
            kind=FactKind.FINDING,
            source_ids=[paper.source_id],
            confidence=Confidence.HIGH,
            qualifiers={"n": 12, "year": 2024},
        ),
        Fact(
            text="Hybrid BM25 + dense retrieval outperforms either alone",
            kind=FactKind.CLAIM,
            source_ids=[paper.source_id],
            confidence=Confidence.MEDIUM,
        ),
        Fact(
            text="Reported gains may not transfer to Chinese literature",
            kind=FactKind.HYPOTHESIS,
            source_ids=[paper.source_id],
            confidence=Confidence.LOW,
            qualifiers={"caveat": "evaluated on English corpora only"},
        ),
    ]
    ledger = FactSet(label="RAG survey", facts=facts)
    recommended = ledger.recommends_page_count(facts_per_page=3.0)
    print(
        f"[facts]  {len(ledger.facts)} total / {len(ledger.citable)} citable  "
        f"density={ledger.density()}  -> recommended_pages={recommended}"
    )

    # 3. the precheck that fixes PPT defect #1 ----------------------------
    requested = 30
    if requested > 2 * max(recommended, 1):
        print(
            f"[guard]  REJECT: requested {requested} pages but only "
            f"{len(ledger.citable)} citable facts support ~{recommended}. "
            "Refusing to generate padded slides."
        )
    else:
        print(f"[guard]  ok: {requested} pages is within budget")

    # 4. execution --------------------------------------------------------
    req = TaskRequest(
        skill="pptx",
        inputs={"title": "RAG 综述汇报", "fact_set": ledger.model_dump()},
        options={"priority": "interactive", "budget": {"max_cost_cny": 2.0}},
    )
    run = Run.from_request(req)

    step = run.add_step("plan", StepKind.LLM)
    step.usage.llm_calls = 1
    step.usage.cost_cny = 0.4
    step.finish(StepStatus.SUCCEEDED, message=f"{recommended} slides planned")

    step = run.add_step("render", StepKind.RENDER)
    step.usage.cost_cny = 0.3
    step.finish(StepStatus.SUCCEEDED, message="pptx written")

    run.recompute_usage()
    run.artifacts.append(
        Artifact(
            kind=ArtifactKind.DECK,
            uri="primary/decks/rag-survey.pptx",
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            origin=Origin.PRIMARY,
            producer="pptx.render",
            title="RAG 综述汇报",
            source_ids=[paper.source_id],
            fact_ids=[f.fact_id for f in ledger.citable],
        )
    )
    run.source_ids = [paper.source_id]
    run.transition(RunStatus.SUCCEEDED)

    print(
        f"[run]    {run.run_id} {run.status.value} "
        f"cost=¥{run.usage.cost_cny} steps={len(run.steps)} breach={run.budget_breach()}"
    )

    # 5. wire -------------------------------------------------------------
    for seq, etype in enumerate(
        [EventType.RUN_CREATED, EventType.ARTIFACT_PRODUCED, EventType.RUN_COMPLETED]
    ):
        frame = RunEvent(run_id=run.run_id, seq=seq, type=etype).to_sse()
        assert frame.count("\n\ndata") == 0, "SSE data must be single-line"
    print(f"[sse]    3 frames emitted, all single-line data")

    # 6. declaration ------------------------------------------------------
    manifest = SkillManifest(
        name="pptx",
        version="0.1.0",
        description="把 FactSet 渲染为可溯源的 .pptx",
        when_to_use="用户要基于研究档案/笔记生成汇报或开题 PPT 时",
        runtime={"kind": RuntimeKind.SUBPROCESS, "entrypoint": "node cli.js", "isolated": True},
        permissions=[{"resource": "fs:primary", "requires_confirm": True}],
        default_budget=Budget(max_cost_cny=2.0),
    )
    print(
        f"[skill]  {manifest.name}@{manifest.version} isolated={manifest.runtime.isolated} "
        f"writes_primary={manifest.writes_primary()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
