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
from typing import NamedTuple, Protocol, Sequence

__all__ = [
    "Runner",
    "RunnerError",
    "RunnerResult",
    "RunnerTimeout",
    "SubprocessRunner",
]


class RunnerError(RuntimeError):
    """执行器自身的问题（命令不存在、无法启动），不是被调用方的业务失败。"""


class RunnerTimeout(RunnerError):
    pass


class RunnerResult(NamedTuple):
    exit_code: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str, timeout_s: float) -> RunnerResult:
        """起一个一次性进程并等待结束。实现必须保证是**新进程**。"""
        ...


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
