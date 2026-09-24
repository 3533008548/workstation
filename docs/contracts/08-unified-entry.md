# 08 · 统一入口（阶段 5）：只监听 loopback 的本地工作台

> 本文定义工作台**对外暴露的唯一网络形态**。改这个接口属于 L2/L3 契约变更。

---

## 1. 形态选择：为什么是标准库 `http.server`

| 候选 | 结论 |
|---|---|
| FastAPI + uvicorn | ❌ 引入 3 个依赖（ASGI/网络栈）换单用户场景用不上的并发模型 |
| 任何对外托管 / 反向代理 | ❌ 违反「数据不出本机」 |
| **标准库 `http.server` + `ThreadingHTTPServer`** | ✅ 零新依赖；守门只要一行白名单；单用户本机足够 |

本项目至今只依赖 pydantic。引入网络栈会撑大「数据不出机器」的最小依赖面，
而收益（并发、生态中间件）在单用户本机场景里为零。

---

## 2. 守门（不可回退）

1. **只绑 loopback。** `create_server()` 对 host 做白名单：
   `127.0.0.1` / `localhost` / `::1`。写成白名单而非「检查是否 `0.0.0.0`」，
   因为漏一个 IPv6 或网卡地址就等于把本机数据放上局域网。越界抛 `ServerError`，
   **拒绝启动**而不是降级警告。
2. **不新增持久化点**（红线 #4）。服务进程内没有任何自有状态；一切经
   `RunStore`。服务只是把已存在的能力暴露成 HTTP，不是第二个状态源。
3. **同源、无 CORS。** 视图由本进程提供，`/api/*` 与 `/` 同源，因此不发任何
   CORS 头 —— 跨域无处可去，也就无从放开。
4. **检索按 context 收窄**（红线 #5）。`/api/retrieve` 默认只查声明了该 context
   的源；`cross_context` 显式为真才跨域。

---

## 3. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 工作台视图（单文件静态页，零构建） |
| GET | `/api/health` | `{status, version, contract_version, home, contexts, skills, sources}` |
| GET | `/api/skills` | 技能 manifest 列表（`SkillManifest.model_dump`） |
| GET | `/api/runs` | 历史 Run；`?status=&skill=&limit=` |
| POST | `/api/runs` | 提交 Run：`{skill, inputs, context, options?}` |
| GET | `/api/runs/<id>` | Run 详情（完整 `Run` 契约） |
| GET | `/api/runs/<id>/events` | 事件回放，SSE（`text/event-stream`） |
| POST | `/api/retrieve` | `{query, context, limit, cross_context}` |

### 状态约定

- `POST /api/runs`：成功 `200`；Run 未成功（门禁拦下 / 失败）`422` —— **Run 仍会
  落盘**，只是这次请求没达成目标；未知技能 / 不合契约 `400`；执行异常 `500`。
- `GET /api/runs/<id>`：不存在 `404`。
- `POST /api/retrieve`：缺 `query` `400`。
- 静态资源：`/static/*` 解析后必须仍在 `static/` 内，否则 `403`。

---

## 4. 前端契约就是 `RunEvent`

`RunEvent` 的字段注释写着 *"The entire frontend contract lives here"*。视图不发明
第二套进度结构，而是直接消费 `to_sse(runtime.events(run_id))` 回放的事件流。
因此**事件语义变了，前端就跟着变** —— 不存在两份进度模型需要同步。

---

## 5. 装配

`build_workbench()` 是唯一装配点，服务与 CLI 共用它：

```
RunStore(home)  →  SkillRuntime{ppt-deck, knowledge-search}  →  RetrievalService{knowledge, markdown, pdf}
```

三源与场景的对应（红线 #5 的落点）：

| 源 | context | 形态 |
|---|---|---|
| knowledge | `interview` | `knowledge-bridge/1` 只读子进程 |
| markdown | `thesis` | 本地目录词法 |
| pdf | `default` | `tools.pdf` 本地解析 |

---

## 6. 启动

```bash
python -m workstation.cli serve --open          # 默认 127.0.0.1:8787
python examples/stage5_server.py                # 自检一遍就退出
python examples/stage5_server.py --serve --open # 保持运行
```

`--host` 传非 loopback 会被拒绝并退出码 2。
