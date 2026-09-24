# 07 · 检索门面（阶段 4）

阶段 4 的检索**按场景（context）隔离**，这是红线 #5「记忆不合并」在检索侧的落点：
检索可以跨源融合排序，但**绝不**把不同场景的用户画像/工作记忆merge。

## 抽象

- `Hit` — 一条命中：`source` / `doc_id`（同源内唯一）/ `title` / `text` / `score` / `page` / `uri` / `section`。
- `RetrievalSource`（协议）— 任一内容源：`name` + `contexts` + `search(query, limit) -> list[Hit]`。
- `rrf_merge(ranked_lists, k=60)` — Reciprocal Rank Fusion，只依赖排序不依赖各源分数量纲，因此能把异构打分（知识库相关度、词法频次）直接融合。
- `RetrievalService` — 注册源；`retrieve(query, context, limit, cross_context)` 只查声明了该 `context` 的源，跨域需 `cross_context=True`。

## 源注册

| 源 | context | 实现 | 场景 |
|---|---|---|---|
| `knowledge` | `interview` | `knowledge-bridge/1`（一次性 Node 子进程，只读） | 面试准备 |
| `markdown` | `thesis` | 本地目录词法召回（标题切片） | 毕设 |
| `pdf` | `default` | `workstation.tools.pdf` 本地解析 + 词法召回 | 通用/补齐 |

新源只需实现 `RetrievalSource` 并注册，RRF 融合层不感知其实现。Markdown/PDF 源
是「Markdown 入 Chroma」的本地轻量形态；将来换嵌入向量（bge-m3 + Chroma）只是
同一源的 drop-in 升级，不影响融合层。

## 已知缺口

- 知识库写入回灌（sink）未接：阶段 3 仅只读，写 Vault 仍归知识库桌面端。
- 跨源去重目前靠 `doc_id`；同一文档若以不同形态出现在多源，融合分会累加（预期行为）。
- 检索不触碰记忆/画像：用户画像仍按场景各自为政（见红线 #5）。
