# 05 · 运行时：Run 落盘、执行形态与事件回放

阶段 1 的后一半。前一半是模型网关（"怎么调模型"），这一半是"一件事怎么跑、
跑完留在哪、断了怎么接上"。

代码在 `workstation/core/runtime/`：`store.py`（落盘）、`executor.py`（执行形态）、
`runtime.py`（编排与回放）。

---

## 1. 为什么必须有这一层

没有它的时候，"跑完之后 Run 在哪"没有唯一答案：PPT 技能自己起进程、自己
维护状态机，Run 只活在返回值里，进程一退出就蒸发。契约里设计了
`checkpoint_ref` 作为跨形态恢复句柄，却没有任何地方写入或读取它——**一个
定义精美、无人使用的死字段**。

更实际的问题是：阶段 3 的知识库桥接是 HTTP 形态，若照阶段 2 的做法再来一遍，
就会有两份私有执行器、两种生命周期，前端运行中心和阶段 5 的断点续跑要同时
适配两套。

职责边界刻意收得很窄：

- **技能**负责做什么（状态机、步骤、产物、预算判断）
- **运行时**负责留下痕迹（落盘、幂等、事件流、恢复）

---

## 2. 存储形状：宽列 + JSON blob

SQLite，WAL 模式。`<home>/derived/runs.sqlite3`。

```sql
runs(run_id PK, task_id, skill, skill_version, status,
     checkpoint_ref, idempotency_key,
     created_at, updated_at, started_at, ended_at, payload)
```

**宽列**是为了查询与恢复——不用反序列化整个 Run 就能回答"现在还有哪些在跑"、
"这个幂等键有没有跑过"。**payload** 存完整 Run 的 JSON，不为 `Step` /
`Artifact` / `Usage` 各建一张表：单用户本机量级下，join 的复杂度换不来任何
东西，而 schema 演进的成本是实实在在的。

`idempotency_key` 上有 partial unique index（只约束非 NULL）。

### 关闭语义

`close()` 之后再用会抛 `RunStoreError("... is closed")`，而不是 sqlite 原生的
`Cannot operate on a closed database`。这是示例里撞出来的：store 关了、
`SkillRuntime` 还拿着它继续 submit，原始错误看不出该怪谁。

---

## 3. 三种执行形态，一个 Result 形状

| 形态 | 用途 | 失败语义 | 恢复句柄 |
|---|---|---|---|
| `subprocess` | PPT 渲染（`pptxgenjs` 模块级状态串扰，必须一次性新进程） | exit code；124 超时 / 127 执行器不可用 | workdir |
| `http` | 知识资产层（长驻本地服务） | HTTP status | 服务端 token |
| `local` | 科研编排（LangGraph）、纯 Python 技能 | 直接抛，不吞 | thread id |

`Executor` 是泛型 Protocol，每种形态有自己的 Job 子类型（`SubprocessJob` /
`HttpJob` / `LocalJob`），类型系统保证"子进程执行器不会收到 URL"。刻意不做
一个塞满可选字段的万能 Job。

三者归一到 `ExecutorOutcome{ ok, kind, exit_code, stdout, stderr, body,
checkpoint_ref, duration_s, error }`。

### 收编而非重写

PPT 技能原本直接依赖 `Runner`（"起进程"的最小接口）。`Runner` 协议已上提到
`core.runtime.executor`，PPT 侧保留 `SubprocessRunner` 实现并 re-export——
**既有调用方与测试一个字都不用改**，但从此它在底座上和 HTTP / local 是同
一种东西。技能内部改为走 `SubprocessExecutor`。

### 外发边界

`HttpExecutor` 默认只允许 `127.0.0.1` / `localhost`，其余一律
`EXECUTOR_REFUSED`。"数据不出本机"的守门处就在这行——一个手滑写进公网地址的
配置不应该安静地成功。

---

## 4. 事件回放

Run 是**状态**，事件是**变迁**。存状态、派生变迁（`events_for`），比存两份
省心，而且断线重连时能原样重放。

```
run.created → run.started → (step.started → step.finished)* →
artifact.produced* → [budget.warning] → run.completed / run.failed /
run.cancelled / approval.required
```

`seq` 严格递增，`to_sse()` 渲染成一段 SSE 文本。`data:` 强制单行（有测试锁定）——
多行会击穿朴素解析器。

---

## 5. 幂等与恢复

- **幂等**：`TaskOptions.idempotency_key` 命中时返回既有 Run，**不重复执行**。
  `SkillRuntime.submit()` 在调用技能之前查库。
- **恢复**：`resume(run_id)` 只对实现了 `resume` 的技能生效，其余抛
  `NotImplementedError`。有意的不对称——一次性渲染重跑比恢复便宜也更安全。
- **失败也要落盘**：未知技能、参数校验失败、预算超限，都会留下一个 FAILED 的
  Run。审计不从成功里挑。

---

## 6. 验证

```bash
python examples/stage1b_runtime.py
```

五段，不需要 Node / 网络 / API key：落盘后重开可读、`checkpoint_ref` 指向可
重放的 `request.json`、幂等键挡住第二次执行、事件回放 8 帧且 `data:` 单行、
三种形态归一。
