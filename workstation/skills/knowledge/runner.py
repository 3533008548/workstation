"""子进程执行器 —— 与真实 `npm run bridge` 之间的那层薄壳。

与 PPT 的 runner 同构：把"进程隔离"这条硬约束收敛成唯一一处可犯错的地方。
知识库桥接是只读检索，无模块级状态串扰，但作为 TS 技能它本来就必须在独立
Node 进程里跑（不可能在 Python 底座内联），所以走 SubprocessExecutor 是自然的。

Windows 上有个真实的坑与 PPT 完全一致：`npm` 实际是 `npm.cmd`，
`CreateProcess` 不能直接执行 `.cmd`，必须先过 `cmd.exe /c`。
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Sequence

# Runner 协议已上提到底座（core.runtime.executor），知识库侧只保留 Windows 特化
# 的实现。re-export 是为了让调用方与测试不必改动一个字。
from workstation.core.runtime.executor import (  # noqa: F401  (re-export)
    Runner,
    RunnerError,
    RunnerResult,
    RunnerTimeout,
)

__all__ = [
    "Runner",
    "RunnerError",
    "RunnerResult",
    "RunnerTimeout",
    "SubprocessRunner",
]


def _resolve(argv: Sequence[str]) -> list[str]:
    if not argv:
        raise RunnerError("empty argv")

    resolved = shutil.which(argv[0])
    if resolved is None:
        raise RunnerError(
            f"{argv[0]!r} not found on PATH; install it or point KnowledgeSkillConfig.command at an absolute path"
        )

    head = [resolved]
    if resolved.lower().endswith((".cmd", ".bat")):
        head = ["cmd.exe", "/c", resolved]
    return [*head, *argv[1:]]


class SubprocessRunner:
    """真实执行器。每次调用一个新进程，不共享任何状态。"""

    def __init__(self, *, env: dict[str, str] | None = None) -> None:
        self._env = env

    def run(self, argv: Sequence[str], *, cwd: str, timeout_s: float) -> RunnerResult:
        command = _resolve(argv)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                env=self._env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunnerTimeout(f"timed out after {timeout_s:g}s: {' '.join(command)}") from exc
        except OSError as exc:  # 权限、路径、损坏的可执行文件
            raise RunnerError(f"failed to start {' '.join(command)}: {exc}") from exc

        return RunnerResult(completed.returncode, completed.stdout, completed.stderr)
