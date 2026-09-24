"""`python -m workstation.cli` —— 当前阶段唯一可启动入口。

提供三个子命令：

    python -m workstation.cli doctor            # 检查本机环境能否真正跑起来
    python -m workstation.cli run ppt --input examples/fixtures/ppt_request.json
    python -m workstation.cli runs list
    python -m workstation.cli runs show <run_id>

说明：本项目现阶段**没有** HTTP 服务、没有 UI、没有定时任务。它是一套
「库 + 契约 + 一个可用技能（PPT）」。这个 CLI 是把「能用」这件事落地的
最小入口 —— 一次调用走完 模型网关无关、页数门禁、子进程桥接、Run 持久化。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - 非 UTF-8 终端时跳过
    pass

from workstation import __version__ as WS_VERSION
from workstation_contracts import CONTRACT_VERSION
from workstation_contracts import RunStatus, TaskOptions, TaskRequest
from workstation.core.runtime import SkillRuntime, open_run_store
from workstation.skills.ppt import (
    PPT_SKILL_NAME,
    PptSkill,
    PptSkillConfig,
    SubprocessRunner,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_HOME = REPO / "runtime"
DEFAULT_PPT_AGENT = Path("D:/develop/project/PPTagent")  # 仅 fallback，env/yaml 优先

# 可选 YAML 配置：有 pyyaml 就读 config/workstation.yaml，否则忽略。
try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def _load_yaml() -> dict:
    if yaml is None:
        return {}
    for p in (REPO / "config" / "workstation.yaml", REPO / "config" / "workstation.example.yaml"):
        if p.exists():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


_CFG = _load_yaml()


# ----------------------------------------------------------------- 配置解析


def resolve_home(args: argparse.Namespace) -> Path:
    raw = getattr(args, "home", None) or os.environ.get("WORKSTATION_HOME") or _CFG.get("home") or str(DEFAULT_HOME)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def resolve_ppt_agent(args: argparse.Namespace) -> Path:
    raw = getattr(args, "ppt_agent", None) or os.environ.get("WORKSTATION_PPT_AGENT")
    if not raw and isinstance(_CFG.get("external"), dict):
        raw = _CFG["external"].get("ppt_agent")
    if not raw and isinstance(_CFG.get("skills"), dict):
        ppt = _CFG["skills"].get("ppt-deck") or {}
        if isinstance(ppt, dict):
            raw = ppt.get("repo")
    if not raw:
        raw = str(DEFAULT_PPT_AGENT)
    return Path(raw).expanduser()


# ----------------------------------------------------------------- 诊断


def _row(tag: str, name: str, detail: str = "") -> bool:
    print(f"[{tag:4}] {name}" + (f"  — {detail}" if detail else ""))
    return tag in ("ok  ", "warn")


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"workstation {WS_VERSION} · contracts {CONTRACT_VERSION}\n")
    ok = True

    ok &= _row("ok  ", f"python {sys.version.split()[0]}")

    home = resolve_home(args)
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".ws-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok &= _row("ok  ", f"home 可写: {home}")
    except Exception as exc:  # noqa: BLE001
        ok &= _row("FAIL", f"home 不可写: {home}", str(exc))

    try:
        store = open_run_store(home)
        store.close()
        ok &= _row("ok  ", f"run store: {home / 'derived' / 'runs.sqlite3'}")
    except Exception as exc:  # noqa: BLE001
        ok &= _row("FAIL", "run store 无法打开", str(exc))

    node = shutil.which("node")
    npx = shutil.which("npx")
    ok &= _row("ok  " if node else "FAIL", f"node: {node or '未找到'}")
    ok &= _row("ok  " if npx else "FAIL", f"npx : {npx or '未找到'}")

    ppt = resolve_ppt_agent(args)
    ok &= _row("ok  " if ppt.exists() else "FAIL", f"PPTAgent 根: {ppt}")
    cli_ts = ppt / "src" / "cli.ts"
    ok &= _row("ok  " if cli_ts.exists() else "FAIL", f"cli 入口: {cli_ts}" + ("" if cli_ts.exists() else "（缺失）"))
    ok &= _row("ok  " if (ppt / "node_modules").exists() else "warn",
               "PPTAgent 依赖已装" if (ppt / "node_modules").exists() else "PPTAgent 依赖未装（先 cd PPTAgent && npm i）")
    ok &= _row("ok  " if (ppt / "spec" / "demo.deck.json").exists() else "warn",
               "demo deck 模板存在" if (ppt / "spec" / "demo.deck.json").exists() else "demo deck 模板缺失")

    key = os.environ.get("WORKSTATION_DEEPSEEK_API_KEY")
    # render 模式不需要 key；plan 模式（让 LLM 生成 DeckSpec）才需要。
    ok &= _row("ok  " if key else "warn",
               "WORKSTATION_DEEPSEEK_API_KEY 已设置" if key else "未设置（render 模式够用；plan 模式需设置）")

    if yaml is None and not (REPO / "config" / "workstation.yaml").exists():
        _row("warn", "未安装 pyyaml：config/workstation.yaml 不会被读取（CLI 参数/环境变量照常可用）")

    print()
    print("结论：环境" + ("就绪，可以跑 run ppt。" if ok else "存在阻塞项，先解决 FAIL。"))
    return 0 if ok else 1


# ----------------------------------------------------------------- 执行


def _build_input(data: dict, args: argparse.Namespace) -> dict:
    keys = ("brief", "facts", "material", "mode", "deck_spec", "requested_pages",
            "facts_per_page", "max_pages", "accept_padding", "theme")
    inputs = {k: data[k] for k in keys if k in data}
    if args.pages:
        inputs["requested_pages"] = args.pages
    if args.accept_padding:
        inputs["accept_padding"] = True
    return inputs


def _print_run(run) -> None:
    print(f"run_id   : {run.run_id}")
    print(f"status   : {run.status.value}")
    if run.error:
        print(f"error    : {run.error}")
    for art in run.artifacts:
        print(f"artifact : {art.uri}")
    print(f"cost     : ¥{run.usage.cost_cny:.4f}  llm_calls={run.usage.llm_calls}")
    print(f"source_ids: {run.source_ids}")
    if run.outputs.get("slide_count") is not None:
        print(f"slides   : {run.outputs['slide_count']}")


def cmd_run(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    ppt = resolve_ppt_agent(args)
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"找不到输入文件：{args.input}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"输入不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    if args.run_skill != "ppt":
        print(f"未知技能子命令：{args.run_skill}（当前仅支持 ppt）", file=sys.stderr)
        return 2

    inputs = _build_input(data, args)
    options = TaskOptions(
        priority=args.priority,
        require_approval=args.require_approval,
        dry_run=args.dry_run,
        idempotency_key=args.idempotency,
    )
    request = TaskRequest(skill=_skill_from_args(args), inputs=inputs, options=options)

    config = PptSkillConfig(workspace_home=home, ppt_agent_root=ppt)
    store = open_run_store(home)
    runtime = SkillRuntime(store, {PPT_SKILL_NAME: PptSkill(config, SubprocessRunner())})
    run = runtime.submit(request)
    _print_run(run)
    return 0 if run.status is RunStatus.SUCCEEDED else 1


def cmd_runs(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    store = open_run_store(home)
    if args.runs_cmd == "list":
        kwargs: dict = {}
        if args.status:
            kwargs["status"] = args.status
        runs = store.list(**kwargs)[: args.limit]
        if not runs:
            print("（无历史 Run）")
            return 0
        for run in runs:
            print(f"{run.run_id}  {run.status.value:9}  skill={run.skill}  "
                  f"steps={len(run.steps)}  cost=¥{run.usage.cost_cny:.4f}")
        return 0
    if args.runs_cmd == "show":
        run = store.get(args.run_id)
        if run is None:
            print(f"无此 run：{args.run_id}", file=sys.stderr)
            return 2
        _print_run(run)
        if run.checkpoint_ref:
            print(f"checkpoint: {run.checkpoint_ref}  （进程崩溃可原位重放）")
        return 0
    return 2


# ----------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ws", description="个人 AI 工作台 CLI（阶段 1b 最小入口）")
    # 全局选项放在最前，所有子命令继承（写在所有子命令之前，如 `ws --home X run ppt`）。
    parser.add_argument("--home", help="工作目录根（默认 workspace/runtime；也可用 WORKSTATION_HOME）")
    parser.add_argument("--ppt-agent", help="PPTAgent 仓库根（也可用 WORKSTATION_PPT_AGENT / config）")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="检查本机环境能否真正跑起来")

    p_run = sub.add_parser("run", help="执行一个技能")
    run_sub = p_run.add_subparsers(dest="run_skill", required=True)
    p_ppt = run_sub.add_parser("ppt", help="渲染 .pptx（PPTAgent 桥接）")
    p_ppt.add_argument("--input", required=True, help="任务输入 JSON（含 brief/facts/deck_spec 等）")
    p_ppt.add_argument("--pages", type=int, help="覆盖 requested_pages")
    p_ppt.add_argument("--accept-padding", action="store_true", help="显式接受框架稿（默认拒绝超量页数）")
    p_ppt.add_argument("--require-approval", action="store_true", help="要求 fs:primary 审批（默认阻塞）")
    p_ppt.add_argument("--dry-run", action="store_true", help="只组装请求不执行")
    p_ppt.add_argument("--priority", default="interactive", choices=["interactive", "batch", "low"])
    p_ppt.add_argument("--idempotency", help="幂等键，重复提交返回同一 Run")

    p_runs = sub.add_parser("runs", help="查看历史 Run")
    runs_sub = p_runs.add_subparsers(dest="runs_cmd", required=True)
    p_list = runs_sub.add_parser("list", help="列出历史 Run")
    p_list.add_argument("--status", help="按状态过滤")
    p_list.add_argument("--limit", type=int, default=20)
    p_show = runs_sub.add_parser("show", help="查看单个 Run 详情")
    p_show.add_argument("run_id")

    return parser


def _skill_from_args(args: argparse.Namespace) -> str:
    # 当前只有 ppt；将来加 subcommand 时映射到 SkillManifest.name。
    return PPT_SKILL_NAME


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "runs":
        return cmd_runs(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
