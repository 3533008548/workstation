# 契约四 · PPT 技能（`ppt-bridge/1`）

> 状态：v0.1.0 · 阶段 2 交付
> 上游：`D:\develop\project\PPTagent`（Node / TypeScript）
> 适配器：`workstation/skills/ppt/`

---

## 1. 为什么是子进程，不是库

PPTAgent 的 `pptxgenjs` 有**模块级状态串扰**：同一 Node 进程内第二次并发渲染
会凭空多出图表文件和关系文件（PPTAgent `REVIEW.md`「故障检测与确定性实验」

实测记录）。所以本技能**不 import** 上游渲染器，而是每次调用起一个新进程、
只做一件事、退出。

这条约束写在三处，互为提醒：

| 位置 | 形式 |
|---|---|
| 本契约 | `runtime.isolated = true`（硬约束，不是优化项） |
| `SkillManifest` | `RuntimeKind.SUBPROCESS` + `isolated=True` |
| 适配器测试 | `test_manifest_pins_process_isolation_and_primary_approval` |

代价是每次调用多一份 Node 启动开销，且不能跨调用共享内存缓存。换来的是
"渲染结果可复现"——这比省几百毫秒重要。

---

## 2. 线上形状

两个方向都是 JSON：

```
底座 ──request.json──▶ PPTAgent（新进程）
底座 ◀──response.json─ PPTAgent（退出）
```

定义在两处，且**两侧都严格**：

- Node：`src/bridge-schema.ts`（zod，`.strict()`）
- Python：`workstation/skills/ppt/contract.py`（pydantic，`extra="forbid"`）

```python
class BridgeRequest(ContractModel):
    api_version: "ppt-bridge/1"
    mode: "plan" | "render"
    brief: BridgeBrief          # title / audience / goal / …
    facts: list[BridgeFact]     # 每条必须带 source_ids（≥1）
    assets: list[BridgeAsset]
    material: str | None        # 文本素材原文，会被包进不可信边界
    page_plan: BridgePagePlan   # requested / facts_per_page / max_pages / accept_padding
    theme: BridgeTheme | None
    deck_spec: dict | None      # mode=render 时必填
    output: BridgeOutput        # pptx_path / deck_path / report_path
    run_id: str | None
```

字段名在线上是 camelCase，Python 侧用别名映射，内部仍是 snake_case。

### 2.1 双向契约测试

两侧各写一遍形状**不是重复劳动**，而是两个方向的漂移告警：

- PPTAgent 给响应加字段 → Python 的 `BridgeResponse` 解析抛错，红灯在**本仓库**；
- 工作台往请求加字段 → PPTAgent 的 zod 抛错。

这正是防腐七条第 2 条"契约测试放在工作台侧"的落地：三方一改，
红灯亮在自己仓库，而不是运行时才炸。

另外两条形状表达不了的约束，由适配器补齐：

1. **成功响应必须自带校验结论**（`specVerification` + `pptxVerification`）。
   形状上它们是可选的（失败响应没有），但"声称 ok 却不给校验结果"等于要求
   调用方盲信 —— 适配器会把它判成 `PPT_BRIDGE_CONTRACT_DRIFT`。
2. **`.strict()`**：字段名拼错必须报错。把 `pagePlan` 拼成 `pageplan` 会被
   静默丢弃并回落默认值，整个页数门禁就此失效。

---

## 3. 页数门禁（P0 缺陷 #1）

### 3.1 问题

旧行为：用户说 30 页就生成 30 页。单句需求被执行成 29 次模型调用之后才
暴露"内容全是填充"。结构校验能证明"内容能看"，证明不了"值得看"。

### 3.2 判据

**信息量值几页**，不是"能生成几页"。可引用事实数除以每页事实数：

```
recommended_pages = clamp(round(citable_facts / facts_per_page), 1, 60)
```

公式只有一份实现，在契约里：`FactSet.recommends_page_count()`。适配器的
预检直接调用它，不另写一份。

### 3.3 判定表

| 情形 | verdict | 处置 |
|---|---|---|
| `requested ≤ recommended` | `ok` | 放行 |
| `requested > recommended`，未接受框架稿 | `exceeds_evidence` | **拦下** |
| `requested > recommended`，`accept_padding=true` | `accepted_padding` | 放行并记录 |
| `requested > max_pages` | `over_budget` | **拦下**，`max_pages` 任何情况不放 |
| 可引用事实为 0，未接受框架稿 | `insufficient_evidence` | **拦下** |

"可引用"= confidence 为 `high`/`medium`。契约里的 `unknown` 在适配器映射为
`low`（不可引用），不猜、不补。

### 3.4 两道防线，一处权威

| 位置 | 作用 | 成本 |
|---|---|---|
| 底座预检 `workstation/skills/ppt/precheck.py` | 早退：不起子进程、不花钱 | 微秒级 |
| PPTAgent 门禁 `src/bridge.ts` | **权威终审**：即使绕过预检也拦得住 | 一次进程启动 |

两侧公式必须一致，用同一组数字向量在两侧测试里对钉
（`tests/test_ppt_precheck.py` ↔ `tests/planner.test.ts`）。**权威在 PPTAgent 侧**：
底座预检错了，只是浪费一次早退机会，不会放行不该放行的请求。

被拒时仍然返回 `pagePlan` 的推荐页数，调用方拿到的是"你只有 4 条事实，
支持约 1 页，可以补材料 / 降到 1 页 / 显式接受框架稿"，不是干巴巴的"不行"。

---

## 4. 来源引用（P0 缺陷 #3 的一半）

### 4.1 收紧

PPTAgent 的 `Fact` 原是 `{id, text, source: string, confidence}`。自由文本来源
**无法校验、无法去重、无法被策略阻断**，这正是它能写出假引用的根因。

现在 `Fact.sourceIds: string[]` **必填且 ≥1**，值是内容寻址 id：

```
source_id = "src_" + sha256(uri + "||" + content_hash_or_empty)[:24]
```

两条实现（`workstation_contracts.derive_source_id` 与 PPTAgent `src/ids.ts`）
**逐字节等价**，用三组固定向量在两侧测试里对钉。这是整个桥接里最容易悄悄
坏掉的地方：算法一偏，同一份材料在两侧得到不同 id，引用链当场断裂。

### 4.2 回填

页面通过 `sourceFactIds` 回引事实，响应里的 `facts[]` 给出反向映射：

```json
{"factId": "fact-mau", "sourceIds": ["src_37c90c90…"], "usedOnPages": ["page-02", "page-03"]}
```

适配器据此回填 `Run.source_ids` 与 `Artifact.source_ids/fact_ids`，
`Run.outputs["used_fact_ids"]` 只含**真正被页面引用**的事实（登记了但没用到
的不算）。

### 4.3 数字白名单更严了

规划阶段的数字白名单**只取 facts 与 Brief**，不含素材全文。素材里出现、
但没有登记成 fact 的数字不允许出现在页面上。这条在两点上比旧实现更严。

`render` 模式**不做**数字溯源：DeckSpec 由调用方提供并自证，而图表/表格里的
派生数字本来就不在事实文本里，拿 facts 当基准会误报。溯源是规划阶段的门禁，
不是渲染阶段的。这条判断是被 `bridge:demo` 的实际误报推着做的。

---

## 5. 素材指令隔离（P0 缺陷 #2）

### 5.1 原问题

旧实现把用户素材直接拼进 **system** 提示（`briefPrompt(sourceText)` 既作为
system 又作为 input），等于把不可信文本放到了最高权威位：素材里一句
"忽略以上要求，直接输出……"就能改写任务定义。

### 5.2 三步修复

1. **素材一律作为 user 消息内容**，system 只放规则；
2. 素材包进 `<untrusted_material id="M1" label="…">` 显式边界，边界内的
   闭合标签会被转义（否则素材可以自己提前关掉边界）；
3. system 中声明该边界内是数据、不是指令，且不得改变输出结构、页数上限与
   数字溯源规则。

命中的可疑模式（覆盖中英文的指令覆盖、提示词外泄、角色扮演、伪 system 角色）
以 `injectionWarnings` 随响应返回。

### 5.3 为什么不是 `safe: boolean`

任何"证明不可能被注入"的接口都是过度承诺。这里做的是**降低成功率 + 让尝试
可观测**，所以返回 warnings 而不是一个布尔值。适配器把它原样放进
`Run.outputs["injection_warnings"]`，不吞掉。

### 5.4 顺带少了一次模型调用

`planFromEvidence` 不再调用 brief 分类模型 —— Brief 由底座给定，模型无法再
通过素材里的文字改写任务定义。注入面少一次往返，也省一次调用。

---

## 6. 适配器映射表

只有三处映射，且都是显式的：

| 契约侧 | 线上侧 | 规则 |
|---|---|---|
| `Fact.confidence = unknown` | `"low"` | 不确定来源 → 不可引用，不猜 |
| `Fact.fact_id` | `BridgeFact.factId` | 同名直传，页面回引用的就是它 |
| `Origin.DERIVED` | — | deck 产物在底座侧标为 derived |

`extra="forbid"` 的意义在这里：上游多给字段不会被透传，而是当场报错。
适配器是唯一允许"知道上游格式"的地方，出了适配器就是工作台契约。

---

## 7. 运行与产物

```bash
python examples/stage2_ppt_bridge.py     # 门禁演示 + 真实桥接（需 Node）
```

产物路径（`WORKSTATION_HOME` 下）：

```
primary/decks/<run_id>/deck.pptx        # 交付物：不可重建 → primary，写入需审批
primary/decks/<run_id>/deck.spec.json   # 同批产出的事实源
derived/runs/<run_id>/request.json      # 发给 PPTAgent 的请求（可复现）
derived/runs/<run_id>/response.json     # PPTAgent 的响应（可审计）
derived/runs/<run_id>/render.report.json
```

审批门禁：`TaskOptions(require_approval=True)` 时执行器**不起子进程**，
直接返回 `RunStatus.WAITING` 与 `outputs["pending_approval"] = "fs:primary"`。
本阶段只做阻塞与可观测，审批队列与 UI 属于阶段 5。

---

## 8. 已知缺口

1. **成本账不平**。PPT 子进程自己调模型，底座看不到它的 token 与费用，只能从
   `modelTrace` 拿到调用次数。`Run.outputs["cost_accounting"]` 直接标成
   `"partial"`，不假装账是平的。（PPTAgent `REVIEW.md` 已知缺陷 #4）
2. **数字只校验"值出现过"，未绑定到 fact 内的精确字段与语义单位**，模型仍可能
   把正确数字放到错误语境。
3. **无真实渲染验证**：只验证 OOXML 结构与几何边界，字体回退与实际字形溢出
   不可证。
4. **素材格式受限**：PDF / 图片 / 扫描件识别不出，属于知识库与解析层（阶段 3）。
