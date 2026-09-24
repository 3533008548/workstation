"""Shared helpers for the PPT skill adapter tests.

Nothing here touches Node, the network or an API key: every execution path runs
against ``FakeRunner``, which is the point of injecting the runner at all.
Imported explicitly by the test modules (``from ppt_fakes import ...``) rather
than injected as fixtures, so the fakes stay ordinary, debuggable objects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from workstation.skills.ppt import PptSkillConfig, RunnerError, RunnerResult

SOURCE_A = "src_" + "a" * 24
SOURCE_B = "src_" + "b" * 24


def bridge_response(**overrides: Any) -> dict[str, Any]:
    """A minimal, contract-valid success response from the PPT bridge."""
    payload: dict[str, Any] = {
        "apiVersion": "ppt-bridge/1",
        "ok": True,
        "mode": "plan",
        "artifacts": {
            "pptxPath": "runtime/primary/decks/run_test/deck.pptx",
            "deckPath": "runtime/primary/decks/run_test/deck.spec.json",
        },
        "slideCount": 4,
        "pageIds": ["page-01", "page-02", "page-03", "page-04"],
        "facts": [
            {"factId": "f1", "sourceIds": [SOURCE_A], "usedOnPages": ["page-02"]},
            {"factId": "f2", "sourceIds": [SOURCE_B], "usedOnPages": ["page-02", "page-03"]},
        ],
        "pagePlan": {
            "requestedPages": 4,
            "effectivePages": 4,
            "recommendedPages": 4,
            "availableFacts": 12,
            "factsPerPage": 3.0,
            "paddingAccepted": False,
        },
        "injectionWarnings": [],
        "modelTrace": [{"task": "plan-page-1", "model": "glm-5.3"}],
        "specVerification": {"ok": True, "issues": []},
        "pptxVerification": {
            "ok": True,
            "slideCount": 4,
            "checks": {
                "slideCount": True,
                "pageNumbers": True,
                "bounds": True,
                "editableTables": True,
                "editableCharts": True,
            },
            "stats": {"slideCount": 4, "shapeTransformCount": 60},
            "issues": [],
        },
    }
    payload.update(overrides)
    return payload


class FakeRunner:
    """A runner that writes a canned response instead of spawning Node."""

    #: Every argv it was asked to run, in order.
    calls: list[tuple[list[str], str, float]]

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        exit_code: int = 0,
        stderr: str = "",
        write_response: bool = True,
        raise_error: Exception | None = None,
    ) -> None:
        self.response = response if response is not None else bridge_response()
        self.exit_code = exit_code
        self.stderr = stderr
        self.write_response = write_response
        self.raise_error = raise_error
        self.calls = []

    def run(self, argv: Sequence[str], *, cwd: str, timeout_s: float) -> RunnerResult:
        self.calls.append((list(argv), cwd, timeout_s))
        if self.raise_error is not None:
            raise self.raise_error

        response_path = Path(argv[-1])
        if self.write_response:
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(
                json.dumps(self.response, ensure_ascii=False), encoding="utf-8"
            )
        return RunnerResult(self.exit_code, "", self.stderr)


def make_config(tmp_path: Path) -> PptSkillConfig:
    return PptSkillConfig(
        workspace_home=tmp_path / "runtime",
        ppt_agent_root=tmp_path / "PPTagent",
    )


__all__ = [
    "FakeRunner",
    "RunnerError",
    "SOURCE_A",
    "SOURCE_B",
    "bridge_response",
    "make_config",
]
