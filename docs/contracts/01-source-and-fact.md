# 契约一 · SourceRef / Fact 统一证据模型

> 状态：草案 v0.1.0 · 单一事实来源 `contracts/python/workstation_contracts/`
> 适用：科研助手、知识资产层、PPT 技能，以及未来任何新技能

---

## 1. 为什么必须先定这一份

三个项目现在是**三套互不相认的"来源"概念**：

| 项目 | 现状 | 问题 |
|---|---|---|
| 科研助手 | 论文记录 + SQLite + Chroma，检索结果带本地路径 | 来源只在检索层存在，产出物（综述、档案）里没有机器可读的引用 |
| 知识库桌面端 | Markdown 索引 + 片段匹配，无向量检索 | 有 `policy/`/`audit/` 管控写入，但没有跨项目的引用标识 |
| PPT Agent | `Fact{ id, text, source: string, confidence }` | **`source` 是自由文本**——无法校验、无法去重、无法被策略阻断。这正是它能凭空生成引用的根因 |

不统一这一层，后面的"统一检索""跨源 RRF""数字溯源"全部是空谈。**先有可引用的原子，才谈得上融合与溯源。**

---

## 2. SourceRef

一条来源 = 一个可被定位、可被哈希校验、可被策略判定的引用点。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source_id` | str | ✓ | 内容寻址：`src_<sha256(uri‖content_hash)[:24]>`。**同 URI + 同内容 → 同 ID**，重复索引幂等且不产生孤儿引用 |
| `source_type` | enum | ✓ | `paper / note / web / dataset / attachment / deck / generated` |
| `uri` | str | ✓ | `WORKSTATION_HOME` 下的相对路径，或绝对 URL。非空强校验 |
| `title` | str | ✓ | |
| `origin` | enum | ✓ | `primary`（不可重建，必须备份）/ `derived`（缓存，可随时删） |
| `producer` | str | ✓ | 铸造它的适配器，如 `research.paper`、`knowledge.note`、`pptx.render`。**出问题时定位责任方** |
| `locator` | object | | `page / section / slide / char_start..end / line_start..end / timestamp_s / extra`。**page、slide 为 1-based**，0 会被拒 |
| `content_hash` | str\|null | | sha256，驱动失效检测与重建 |
| `language` | str\|null | | 中英混合检索需要 |
| `created_at` / `updated_at` | datetime | ✓ | |
| `meta` | dict | | 适配器逃生舱 |

**不变式**

1. `SourceRef` 是 **frozen**。引用一旦被 Deck / 笔记 / Run 引用，就不可原地修改。更正 = `revise()` 产生新版本（`updated_at` 自增），`source_id` 保持不变。
2. `extra="forbid"`。适配器必须**逐字段显式映射**，不允许把未知字段透传。第三方涨字段 → 在适配层报错，而不是运行时静默。
3. `is_local` = URI 里没有 `://`。这是**外发策略的判定开关**（见合规章节）。

---

## 3. Fact

一条事实 = 一个原子陈述 + 至少一个可追溯来源。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fact_id` | str | ✓ | `fact_<ms>_<rand>` |
| `text` | str | ✓ | |
| `kind` | enum | | `claim / metric / method / finding / definition / quote / hypothesis` |
| `source_ids` | list[str] | ✓ | **最少 1 条，自动去重，空串拒绝** |
| `confidence` | enum | | `high / medium / low / unknown` |
| `qualifiers` | dict | | 作用域条件：`n=`、`year=`、`population=`、`unit=`、`caveat=` |
| `evidence` | str\|null | | 原文片段 |
| `created_at` / `meta` | | | |

**不变式**

- **Fact 绝不携带自由文本来源。** 这是与 PPT Agent 旧模型最本质的差别，也是不可回退的红线。
- `is_citable`：`confidence ∈ {high, medium}`。Deck 渲染的准入门槛——低置信事实不进 PPT。

### FactSet：解决 PPT 缺陷 #1

`FactSet` 提供两个**可计算**的量，把"这份材料该做多少页"从主观判断变成算术：

```python
fs.density()                      # 每千字符的事实条数
fs.recommends_page_count(3.0)     # citable 事实数 / 每页事实数，钳制在 1..60
```

PPT Agent 的缺陷 #1 是"一句话生成 30 页填充内容"，根因是**生成前没有内容量预检**。有了 FactSet，预检变成：

```
citable == 0        → 拒绝生成，回到“先补充证据”
requested_pages > 2 × recommends_page_count()
                    → 需要用户确认，或直接回退到推荐页数
```

**顺带解决的是素材来源**：你说 PPT 素材来自科研汇报与开题报告——那么 `FactSet` 正好可以直接由科研助手的"证据与假设账本"生成，`facts[]` + `sourceFactIds` 与 PPT 的数字溯源是**同构**的，无需翻译层。

---

## 4. 三方适配映射（这是防腐层的具体作业面）

| 来源 | 适配器 | 映射要点 |
|---|---|---|
| 科研助手论文 | `research.paper` | `uri = primary/papers/<id>.pdf`；`source_type=paper`；`origin=primary`；`locator.page` 来自 PDF 解析；`content_hash` = 文件 sha256 |
| 科研助手网页/API 结果 | `research.web` | `uri` = 绝对 URL；`origin=derived`（可重新拉取）；`is_local=False` → 命中外发策略例外清单 |
| 知识库笔记 | `knowledge.note` | `uri = primary/vault/<相对路径>.md`；`source_type=note`；`locator.line_start..end`；`content_hash` = 正文 sha256 |
| 知识库附件 | `knowledge.attachment` | `source_type=attachment`；图片目前**外发 DeepSeek Vision**，需经 §5 的 `net` 放行；PDF 当前**未被解析**（能力缺失，由科研助手本地链路填补） |
| PPT 既有 `Fact` | `pptx.fact` | ⚠️ `source: string` 无法自动映射 → 适配器必须**显式失败**并要求重建，不得用 `title=source` 蒙混 |
| Deck 产出 | `pptx.render` | `artifact.origin=primary`，`source_ids` 回填自输入 FactSet |

---

## 5. 合规硬约束（与"数据不出本机"直接冲突的部分）

> **更正（2026-09-24）**：早期结论写的是"知识库把本地文件外发给 GLM-OCR / GLM 视觉"。
> 该结论已过期。核实代码后：**知识库桌面端已经把 GLM 整体下线，改走 DeepSeek。**
> 但外发风险并未消失，只是换了厂商；而且 PDF 从"外发解析"变成了**根本不解析**。

核实到的实际状态：

| 结论 | 证据 |
|---|---|
| `GlmClient` 已成死代码 | `core/services/glm-client.ts` 定义了类，**全项目无任何 import** |
| 运行期走 DeepSeek | `desktop-attachment-service.ts:145` 实际 `new DeepSeekVisionClient(...)`；`desktop-knowledge-system-service.ts` 文本侧用 `DeepSeekClient` |
| 配置已清除 | `src/main.ts:848` 写 `.env` 时**主动过滤** `GLM_API_KEY` / `GLM_MODEL` |
| **PDF 不再解析** | `markFailed(path, "DeepSeek Vision 暂不支持 PDF 解析")`，PDF 只计入 `unsupportedPdfCount` |

因此当前真实的外发面是：

1. **图片外发（仍在）** — 附件图片被 `binary.toString("base64")` 后 POST 到 DeepSeek Vision 云端。这是**唯一仍在运行的文件外发路径**。
2. **PDF 解析缺失（新问题）** — 不是"不该外发"，而是"根本没有本地解析能力"。

**契约层处置**：`SourceRef.is_local == True` 且目标能力需要云端处理时，必须由 `Permission{resource: "net", requires_confirm: True}` 显式放行，**默认拒绝、显式授权**。这条对图片路径同样适用。

**命名债（必须一并清理，否则会误导后续维护者）**：策略动作至今仍叫 `sendToGlm`，出现在 `policy-engine.ts` 与三个 `desktop-*` 服务里，而错误提示已经写"DeepSeek Vision"；`agent-runtime.ts` 的工具描述仍写"按权限、GLM 限速和每日预算处理"；`services/env.ts` 的模板注释仍写"PDF OCR 与图片解析使用 GLM"。**代码已迁移，名字没跟上。**

**对阶段 3 的影响（变得更好了）**：原本要"把 GLM-OCR 换成本地解析"，现在变成"**补一个本来就没有的 PDF 解析能力**"。科研助手已有 PyMuPDF 双栏重排 + pdfplumber 表格，直接复用即可——这从"替换"降级为"填补空白"，阻力更小，收益不变，仍是两个项目之间第一处真实的能力复用。

### GLM 在网关里的定位

GLM **没有被从网关移除**，因为它只对知识库死了，没有全局死：

- 知识库：已全面迁移 DeepSeek（文本 + 视觉）
- 科研助手：只配了 DeepSeek（`config.yaml`：`model: deepseek-flash`）
- **PPTAgent：仍在用 GLM** — `src/llm.ts` 的 `MODEL_ROUTES` 把 `plan` / `compress` 主模型设为 `glm-5.3`

所以网关里 **DeepSeek 是默认车道，GLM 是备用/专长车道**，且全部由配置驱动，未来任何一边再迁移都只改 yaml。

---

## 6. 版本与破坏性变更

| 变更 | 级别 | 后果 |
|---|---|---|
| 新增 `SourceType` / `FactKind` 枚举成员 | L1 | 需回归，兼容 |
| **重命名或删除枚举成员** | **L3** | 全量适配器失效，必须同步改造 |
| 新增可选字段 | L0 | 无感 |
| 把可选字段改为必填 | L2 | 改适配器 |
| 改 `derive_source_id` 算法 | **L3** | **全部既有 `source_id` 失效，库内引用全断** |
| 放宽 `extra="forbid"` | L2 | 削弱防腐保证，需评审 |

> 类比提醒：**换嵌入模型也是一次 L3**。`all-MiniLM-L6-v2` → `bge-m3` 维度 384→1024，Chroma 既有索引全部失效，必须新建 collection 全量重建，不能原地换。收益仍是最高的，但要走迁移流程（见契约三 §6）。
