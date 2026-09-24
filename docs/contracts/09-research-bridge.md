# 09 · 科研助手接入（阶段 6，毕设域）

> 对端：`D:\develop/academic/research_agent`，容器化 FastAPI 服务，
> `docker-compose.yml` 把它发布在 **`127.0.0.1:7860`**（默认就只绑 loopback）。

---

## 1. 形态：HTTP，不是子进程

这是与 PPT / 知识库**有意的不对称**，不是例外：

| 项目 | 形态 | 理由 |
|---|---|---|
| PPTAgent | 子进程 `ppt-bridge/1` | `pptxgenjs` 模块级状态串扰，**必须**进程隔离 |
| 知识库 | 子进程 `knowledge-bridge/1` | 无 HTTP 服务；`core/` 纯 Node，一次性调用即可 |
| **科研助手** | **HTTP `/api/v1`** | 自带编排/队列/向量库，本就是长驻服务；不该为它再造一层 CLI |

工作台因此第一次用上 `RuntimeKind.HTTP`，也就是阶段 1b 就已经备好的
`HttpExecutor` —— **非 loopback 的 base_url 在执行器层就被拒绝**
（`EXECUTOR_REFUSED`），不需要适配器自觉。数据不出本机的守门处在这里。

**绝不 import 对端代码。** 对端依赖 chromadb / langgraph / redis /
sentence-transformers；把它们拉进底座会摧毁「只依赖 pydantic」的最小依赖面。

---

## 2. 异步语义：202 + 句柄

对端 `POST /api/v1/runs` 返回 **202 Accepted**，研究在容器里跑。所以：

* 工作台这个 Run 代表「**提交**」这一件事，提交成功即 `succeeded`；
* `checkpoint_ref` = 对端 `stream_url`（绝对地址）——跨进程恢复句柄；
* `outputs["agent_run_id"]` = 对端 run_id；
* `ResearchSkill.resume()` 实现阶段 1b 的 `Resumable`：轮询对端，把终态并回本 Run。

也就是说「提交」和「跑完」是两件事但同一个 Run；进程崩了也能靠句柄接回去。

**记账**：`outputs["cost_accounting"] = "delegated"`。模型调用发生在对端、由对端
记账；工作台这一跳 `llm_calls = 0`。不要在对端之外重复计一次成本。

---

## 3. 端点映射

| 工作台方法 | 对端 | 说明 |
|---|---|---|
| `health()` | `GET /api/v1/health` | 连不上返回 `False`（供 doctor / 门禁），**不抛** |
| `create_session(title)` | `POST /api/v1/sessions` | 对端 research 需要 `session_id` |
| `start_research(sid, query, scope)` | `POST /api/v1/runs` | `kind=research`，202 |
| `get_run(run_id)` | `GET /api/v1/runs/{id}` | resume 用 |
| `list_papers()` | `GET /api/v1/workspace/papers` | 论文库（检索源用） |

`scope` 取 `both | local | public`（对端 `RunCreateRequest.scope`）。

**契约姿态**：工作台发出的请求用 `extra="forbid"`；对端返回的载荷用
`extra="ignore"` —— 对端独立演进，它多返回一个字段不该让工作台崩。这是防腐层的
正常姿态。

---

## 4. 检索源的能力边界（重要）

`ResearchRetrievalSource`（`contexts = ["thesis"]`）拉对端论文库做**标题级词法**召回。

**只有标题可检索。** `/workspace/papers` 返回 `{paper_id, title, chunks,
indexed_at, relation_count}` —— 没有摘要也没有正文。真正的语义检索在对端的
Chroma 里。别把它当成全文检索用。

服务不可用时返回**空命中**而不是抛异常：Docker 没起不该让一次跨源检索整个失败，
只是这一源缺席（与 `KnowledgeRetrievalSource` 同策略）。

`thesis` 场景因此有两个源：`markdown`（本地笔记）+ `research`（论文库），RRF 会把
两者的排序融合——注意这只融合**排序**，不合并记忆（红线 #5）。

---

## 5. 启动

```bash
# 1. 先起科研助手容器（Docker Desktop 必须开着）
cd D:/develop/academic/research_agent && docker compose up -d

# 2. 确认工作台能看到它
python -m workstation.cli doctor        # 科研助手那一行应为 ok

# 3. 提交研究任务（异步）
python -m workstation.cli run research --query "知识蒸馏在边缘设备上的应用" --scope both

# 4. 演示（无需 Docker，内置桩服务）
python examples/stage6_research.py --demo
```

服务未起时 `doctor` 记为 **warn 而非 FAIL**：它只让毕设域缺席，不阻塞 PPT / 知识库。
