"""子进程执行器 —— 与真实 `npx tsx` 之间的那层薄壳。

存在的意义是把"进程隔离"这条硬约束变成代码里唯一一处可以犯错的地方：
PPTAgent 的 `pptxgenjs` 有模块级状态串扰，同一进程内第二次并发渲染会
凭空多出图表文件，所以每次调用必须是**新进程**。runner 只提供一种能力
——起一个进程、拿到退出码和输出——测试用一个假 runner 就能把整条技能
执行路径跑完，不需要 Node、不需要网络、不需要 API key。

Windows 上有个真实的坑：`npx` 实际是 `npx.cmd`，而 `CreateProcess` 不能
直接执行 `.cmd`（那是 `ShellExecute` 才会帮忙做的事），必须先过
`cmd.exe /c`。CMDN 环境里这个坑不踩一次是不会知道的。
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Sequence

# Runner 协议已上提到底座（core.runtime.executor），PPT 侧只保留 Windows 特化
# 的实现。这里 re-export 是为了让既有调用方与测试不必改动一个字 —— 但从此
# "起一次性进程"在底座上和 HTTP / local 是同一种东西。
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
            f"{argv[0]!r} not found on PATH; install it or point PptSkillConfig.command at an absolute path"
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
