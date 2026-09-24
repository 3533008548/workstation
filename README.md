# Workstation · 个人 AI 工作台

单用户、本机部署、数据不出机器（LLM 可走云）的个人 AI 工作台底座。
整合三个既有项目：科研助手、知识资产层（原 Obsidian/桌面端知识库）、PPT 技能。

> 上游分析见工作区：`个人AI工作台-架构梳理.md`、`个人AI工作台-技术选型.md`。
> 本仓库只承载**代码与契约**，不承载数据（数据在 `runtime/`，已 gitignore）。

---

## 当前状态：阶段 0–2 已完成，下一步阶段 3

| 阶段 | 内容 | 状态 |
|---|---|---|
| **0 · 契约先行** | SourceRef/Fact、Run/Task、数据目录规范 | ✅ 已完成 |
| **1a · 模型网关** | 统一 LLM 接入 + 预算治理 | ✅ 已完成 |
| **1b · 运行时** | Run 落盘 + 执行形态归一 + 事件回放 | ✅ 已完成 |
| **2 · PPT 技能化** | CLI → `ppt-bridge/1` 子进程接口，补 P0 缺陷 | ✅ 已完成 |
| 3 · 知识库桥接 | `HttpExecutor` 已就绪，待接 TS 服务化 + 本地 PDF 解析 | ⬜ 下一步 |
| 4 · 统一检索与记忆 | 跨源 RRF、Markdown 入 Chroma、画像合并 | ⬜ |
| 5 · 统一入口 | 前端工作台视图 | ⬜ |

---

## 架构一句话

**Python 为主栈的共享底座 + 技能化。**

- 主栈 Python：科研助手是唯一具备生产级编排/队列/向量检索/可观测性的项目，把它抽成底座是"拆"；把另外两个搬进 Python 是重写 13K 行 TS。
- TS 侧**不搬运、只桥接**：知识库作为长驻 HTTP 服务暴露能力，PPT 作为一次性子进程调用（`pptxgenjs` 有模块级状态串扰，**必须进程隔离**）。
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
  skills/ppt/                     # 阶段 2：PPTAgent 适配器（子进程 + ppt-bridge/1）
docs/contracts/                   # 五份契约文档
scripts/
  gen_schema.py                   # Python → JSON Schema
  verify.sh                       # 契约闸门：schema 漂移检查 + 全量测试
examples/
  stage0_smoke.py                 # 来源 → 事实 → 页数预检 → Run → SSE
  stage1_gateway.py               # 网关在故障、预算与并发下的行为
  stage1b_runtime.py              # 落盘 / 恢复句柄 / 幂等 / 事件回放
  stage2_ppt_bridge.py            # 页数门禁演示 + 真实 PPTAgent 渲染
config/workstation.example.yaml
```

---

## 快速开始

```bash
# 1. 依赖（建议 venv）
pip install -e contracts/python[dev]

# 2. 契约闸门（生成 schema + 跑全量测试）
bash scripts/verify.sh

# 3. 本地配置
cp config/workstation.example.yaml config/workstation.yaml
export WORKSTATION_API_TOKEN=<随机串>

# 4. 阶段 2 端到端（真实渲染需要本机 Node）
python examples/stage2_ppt_bridge.py
```

Python 最低 3.11。

---

## 四条不可回退的红线

1. **Fact 必须携带 `source_ids`（≥1），绝不携带自由文本来源。**
   PPT Agent 旧模型 `Fact{source: string}` 无法校验、无法去重、无法被策略阻断，是它能凭空生成引用的根因。
2. **Vault（`primary/vault/`）是唯一写入通道。** 其它技能（含科研助手）一律只读；要产出笔记就发 TaskRequest。
3. **PPT 渲染必须进程隔离。** `runtime.isolated: true` 是硬约束，不是优化。
4. **Run 只能有一个落盘处。** 技能不得自己持久化自己的执行状态；新增执行形态
   必须实现 `Executor`，而不是在技能里另起一份。`HttpExecutor` 默认拒绝非
   loopback 目标——数据不出本机的守门处。

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
