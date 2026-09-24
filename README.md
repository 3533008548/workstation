# Workstation · 个人 AI 工作台

单用户、本机部署、数据不出机器（LLM 可走云）的个人 AI 工作台底座。
整合三个既有项目：科研助手、知识资产层（原 Obsidian/桌面端知识库）、PPT 技能。

> 上游分析见工作区：`个人AI工作台-架构梳理.md`、`个人AI工作台-技术选型.md`。
> 本仓库只承载**代码与契约**，不承载数据（数据在 `runtime/`，已 gitignore）。

---

## 当前状态：阶段 0–4 已完成，下一步阶段 5

| 阶段 | 内容 | 状态 |
|---|---|---|
| **0 · 契约先行** | SourceRef/Fact、Run/Task、数据目录规范 | ✅ 已完成 |
| **1a · 模型网关** | 统一 LLM 接入 + 预算治理 | ✅ 已完成 |
| **1b · 运行时** | Run 落盘 + 执行形态归一 + 事件回放 | ✅ 已完成 |
| **2 · PPT 技能化** | CLI → `ppt-bridge/1` 子进程接口，补 P0 缺陷 | ✅ 已完成 |
| **3 · 知识库桥接** | `knowledge-bridge/1` CLI（esbuild 打包 core/，只读检索）+ SourceRef 映射 + 双向契约 | ✅ 已完成 |
| **4 · 按场景隔离的检索与记忆** | `RetrievalService`（RRF 融合）+ 三源（knowledge/interview、markdown/thesis、pdf/default）+ 本地 PDF 解析工具层；**记忆不合并** | ✅ 已完成 |
| 5 · 统一入口 | 前端工作台视图 | ⬜ |

---

## 架构一句话

**Python 为主栈的共享底座 + 技能化。**

- 主栈 Python：科研助手是唯一具备生产级编排/队列/向量检索/可观测性的项目，把它抽成底座是"拆"；把另外两个搬进 Python 是重写 13K 行 TS。
- TS 侧**不搬运、只桥接**：知识库通过一次性 CLI 子进程暴露能力（`knowledge-bridge/1`，用其既有 esbuild devDep 打包 `core/`，无 HTTP 服务、无 tsx/zod 依赖）；PPT 同样作为一次性子进程调用（`pptxgenjs` 有模块级状态串扰，**必须进程隔离**）。两者形态对称，都走 `SubprocessExecutor`。
- 技能契约字段兼容社区 **SKILL.md** frontmatter，另加 `runtime` + `permissions`。

---

## 目录

```
contracts/
  python/workstation_contracts/   # 契约单一事实来源（Pydantic v2）
  schema/                         # 生成的 JSON Schema，已提交
  ts/src/index.ts                 # TS 镜像类型
workstation/
  core/model_gateway/             # 阶段 1a：唯一 LLM 入口（路由/熔断/准入/预算）
  core/runtime/                   # 阶段 1b：Run 落盘 / 执行形态 / 编排与回放
  core/retrieval/                 # 阶段 4：检索门面（RRF 融合 + 源注册 + 按 context 收窄）
  skills/ppt/                     # 阶段 2：PPTAgent 适配器（子进程 + ppt-bridge/1）
  skills/knowledge/               # 阶段 3：知识库检索适配器（子进程 + knowledge-bridge/1）
  tools/pdf/                      # 阶段 4：本地 PDF 解析工具（表格感知+双栏重排，填补知识库 PDF 缺口）
docs/contracts/                   # 五份契约文档
scripts/
  gen_schema.py                   # Python → JSON Schema
  verify.sh                       # 契约闸门：schema 漂移检查 + 全量测试
examples/
  stage0_smoke.py                 # 来源 → 事实 → 页数预检 → Run → SSE
  stage1_gateway.py               # 网关在故障、预算与并发下的行为
  stage1b_runtime.py              # 落盘 / 恢复句柄 / 幂等 / 事件回放
  stage2_ppt_bridge.py            # 页数门禁演示 + 真实 PPTAgent 渲染
  stage3_knowledge_bridge.py      # 知识库只读检索端到端（真实 Node 子进程）
config/workstation.example.yaml
```

---

## 快速开始

```bash
# 1. 依赖（建议 venv）
pip install -e .[dev]

# 2. 契约闸门（生成 schema + 跑全量测试）
bash scripts/verify.sh

# 3. 本地配置（可选；CLI 也认环境变量与默认值）
cp config/workstation.example.yaml config/workstation.yaml
export WORKSTATION_API_TOKEN=<随机串>

# 4. 阶段 2 端到端（真实渲染需要本机 Node）
python examples/stage2_ppt_bridge.py

# 5. 阶段 3 端到端（知识库只读检索；无需 API key、不出网、不写 Vault）
python examples/stage3_knowledge_bridge.py --query "langgraph 和 langchain 区别"

# 6. 阶段 4 端到端（按场景隔离的跨源检索：knowledge/interview、markdown/thesis、pdf/default）
python examples/stage4_retrieval.py

# 7. 本地 PDF 解析工具（表格感知 + 双栏重排；填补知识库 PDF 缺口）
python -m workstation.tools.pdf extract examples/fixtures/pdfs/sample.pdf --json
```

Python 最低 3.11。

---

## 怎么启动（CLI）

本项目现阶段**没有** HTTP 服务、UI 或定时任务。它是一套「库 + 契约 + 两个
可用技能（PPT、知识库检索）」。`workstation/cli.py` 是当前唯一可启动入口——它把
「能用」这件事落地：一次调用走完 预检 → 子进程桥接 → Run 持久化。

```bash
# 前置
#   - 本机 Node（已装）
#   - PPTAgent 依赖已装：cd D:/develop/project/PPTagent && npm i
#   - 知识库依赖已装：cd D:/develop/agent for obsidian && npm i
#   - 渲染需要云模型：export WORKSTATION_DEEPSEEK_API_KEY=...

# 1. 先看本机环境能否真跑起来
python -m workstation.cli doctor

# 2. 渲染一份 PPT（demo 模板只有 4 条事实，按页数门禁需显式接受框架稿）
python -m workstation.cli run ppt --input examples/fixtures/ppt_request.json --accept-padding

# 3. 检索知识库 Vault（只读；vault 必须是 Windows 原生绝对路径，勿传 /d/...）
python -m workstation.cli run knowledge \
  --input examples/fixtures/knowledge_request.json

# 4. 查历史 Run（即使被门禁拦下或失败，也会落盘，可审计）
python -m workstation.cli runs list
python -m workstation.cli runs show <run_id>

# 5. 按场景隔离的跨源检索（阶段 4）
#    interview → 只查知识库；thesis → 只查 Markdown 目录；default → 只查 PDF 目录
#    跨场景是显式 opt-in：加 --cross-context 才跨域（红线#5：记忆不合并）
python -m workstation.cli retrieve --query "langgraph 和 langchain 区别" --context interview
python -m workstation.cli retrieve --query "知识蒸馏" --context thesis
python -m workstation.cli retrieve --query "注意力机制" --context interview --cross-context
```

产物落在 home（默认 `runtime/`）下：

| 路径 | 内容 |
|---|---|
| `primary/decks/<run_id>/deck.pptx` | 交付物（不可重建） |
| `derived/runs/<run_id>/` | 中间态：request / response / 渲染报告 |
| `derived/runs.sqlite3` | Run 历史（RunStore，阶段 1b 落盘） |

> 注：本进程（工作台侧）不调模型，但 **PPTAgent 的 bridge 命令在渲染前会走
> `compress` 路由做事实摘要**，所以 `WORKSTATION_DEEPSEEK_API_KEY` 必须设置，
> 否则会在模型调用处失败（`BRIDGE_FAILED`）。

知识库检索（`run knowledge`）则相反：**纯本地只读、不调模型、不出网、不写
Vault**，因此不需要任何 API key。知识库仓库没有 tsx/zod，桥接用其既有
`esbuild` devDep 打包 `core/`（CLI 命令 `npm run bridge`），底座侧经
`SubprocessExecutor` 调用。vault 路径会原样传给 Windows 原生 `node`，**必须是
Windows 原生绝对路径**（如 `D:/develop/...`），传 Git-Bash 形式的 `/d/...` 会被
Node 误解为当前盘根目录而检索失败。

`--home` / `--ppt-agent` / `--knowledge-root` 为全局选项（也可经
`WORKSTATION_HOME` / `WORKSTATION_PPT_AGENT` / `WORKSTATION_KNOWLEDGE_ROOT` 或
config 提供），需写在子命令之前，如
`python -m workstation.cli --knowledge-root D:/develop/agent for obsidian run knowledge --input ...`。

---

## 四条不可回退的红线

1. **Fact 必须携带 `source_ids`（≥1），绝不携带自由文本来源。**
   PPT Agent 旧模型 `Fact{source: string}` 无法校验、无法去重、无法被策略阻断，是它能凭空生成引用的根因。
2. **Vault（`primary/vault/`）是唯一写入通道。** 其它技能（含科研助手）一律只读；要产出笔记就发 TaskRequest。
3. **PPT 渲染必须进程隔离。** `runtime.isolated: true` 是硬约束，不是优化。
4. **Run 只能有一个落盘处。** 技能不得自己持久化自己的执行状态；新增执行形态
   必须实现 `Executor`，而不是在技能里另起一份。`HttpExecutor` 默认拒绝非
   loopback 目标——数据不出本机的守门处。
5. **记忆 / 画像按场景（context）隔离，绝不合并。** 工作台是多个场景的复用入口，
   不是单一合并记忆体。毕设（科研助手域）与面试（知识库域）的成功标准相反
   （深 vs 广、慢 vs 快、单作者 vs 多角色），跨域合并只会相互污染。每个 Run、
   每条记忆 / 画像都带 `context` 标签；检索默认按 `context` 收窄，跨域检索是
   显式 opt-in，永不默认。知识库作为 sink 时，产出按 `context` 分区存放，记忆
   仍不合并。

---

## 契约变更规则

对外契约不变 → 内部随便改；契约一变 → 必须同步。分级：

| 级别 | 含义 |
|---|---|
| L0 | 内部实现变化，契约不变 |
| L1 | 可能影响行为，跑测试确认 |
| L2 | 契约变了但语义可映射，改适配器一处 |
| L3 | 破坏性，必须同步改造 |

改 `derive_source_id`、重命名枚举成员、换嵌入模型维度 —— 均为 **L3**。

详见 `docs/contracts/03-data-layout.md` §7。
