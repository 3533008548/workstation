# 契约三 · 数据目录规范与防腐设计

> 状态：草案 v0.1.0

---

## 1. 根目录

```
WORKSTATION_HOME  环境变量，默认  <repo>/runtime
```

默认与仓库同目录（便于整体备份），但**数据与代码必须物理分离**：`runtime/` 整体进 `.gitignore`，代码里不得出现任何指向 `runtime/` 之外的用户数据写入。

## 2. 目录树

沿用科研助手已经验证过的 `primary` / `derived` 分离——这是本项目里**最值得直接继承的一条设计**：

```
$WORKSTATION_HOME/
├── primary/                    # 不可重建 · 必须备份 · 写入需审批
│   ├── db/workbench.db         # SQLite (WAL)：Run / Step / Artifact / 账本
│   ├── vault/                  # 知识资产层 —— 唯一写入通道
│   │   ├── inbox/              # 未整理输入
│   │   ├── notes/              # 主题笔记
│   │   ├── daily/              # 日志
│   │   └── wiki/               # 编译产物
│   ├── papers/                 # 论文 PDF + 解析产物
│   ├── decks/                  # PPT 产出（.pptx + DeckSpec.json）
│   ├── attachments/
│   └── profile.md              # 用户画像（三个项目各写了一套，此处收敛为一份）
│
├── derived/                    # 可重建 · 不备份 · 可随时 rm -rf
│   ├── chroma/                 # 向量库（注意：换嵌入模型必须换 collection）
│   ├── cache/                  # LLM 响应、HTTP 缓存
│   ├── artifacts/              # 中间产物
│   ├── runs/                   # 子进程 workdir、checkpoint
│   ├── logs/
│   └── redis/
│
├── meta/
│   ├── migrations.json         # schema 版本与已执行迁移
│   └── state.json
│
└── config/
    └── workstation.yaml        # 从 workstation.example.yaml 复制
```

**判定规则（写任何文件前自问）**：删掉它，能否用 `primary/` 里的数据重算出来？
能 → `derived/`；不能 → `primary/`。

## 3. 三个既有项目的数据根如何接入

| 项目 | 现状数据根 | 接入方式 |
|---|---|---|
| 科研助手 | `D:\develop\academic\research_agent\runtime\{primary,derived,meta}` | **结构已一致**，通过 `WORKSTATION_HOME` 指向它即可；待其稳定后再迁移目录 |
| 知识库桌面端 | Electron userData 下的 Vault | 阶段 3 桥接：先只接**只读检索**，Vault 写入权收归知识资产层 |
| PPT Agent | 散落的 `state/` | CLI 服务化后，产出统一落 `primary/decks/`，中间态落 `derived/runs/` |

迁移期允许 `WORKSTATION_HOME` 之外存在旧路径，但**所有新写入必须走新根**。

## 4. 单侧写入原则

> **Vault（`primary/vault/`）只能由知识资产层写入。其它一切技能，包括科研助手，一律只读。**

这一条同时消灭两类问题：

1. **并发写冲突**——科研助手写笔记、桌面端写笔记、PPT 写笔记，三方叠加必然打架；
2. **权限口径不一致**——`policy/` 的敏感目录判定只在知识资产层生效一次，其它路径绕不过去。

其它技能要产出笔记，正确姿势是**发一个 TaskRequest 给知识资产层**，而不是自己动手写文件。

## 5. 备份策略（由 primary/derived 分离直接得出）

| 类别 | 策略 |
|---|---|
| `primary/` | 整体备份。SQLite 用 `VACUUM INTO` 得一致性快照，不要直接拷 db-wal |
| `derived/` | 不备份。`chroma/` 重建成本高但可重建 |
| `meta/migrations.json` | 随 primary 备份 |
| `config/workstation.yaml` | 单独保管（含 token） |

## 6. 两条必须走流程的迁移

### 6.1 换嵌入模型（L3）

`all-MiniLM-L6-v2`（英文为主，384 维）→ `bge-m3` / `gte-multilingual-base`（1024 维）。中文科研检索质量提升显著，但**维度变化使既有 Chroma 索引全失效**：

1. 新建 collection `papers_bge_m3`，**不复用旧名**
2. 后台全量重建索引
3. 双读校验：新旧同时召回，比对 top-k 重合度
4. 切换读路径
5. 观察一个周期后删旧 collection

严禁原地改名或原地换模型。

### 6.2 DeckSpec 版本升级（L3）

`DeckSpec.version` 已存在，必须**强制校验**：版本不匹配的 `patch` / `verify` 直接拒绝，不做猜测式兼容。

## 7. 变更影响分级（回答"其他项目改动会不会波及我"）

| 级别 | 含义 | 处置 |
|---|---|---|
| **L0** | 内部实现变化，对外契约不变 | 无需动作 |
| **L1** | 可能影响行为 | 跑契约测试确认 |
| **L2** | 契约变了但语义可映射 | 改工作台侧适配器一处 |
| **L3** | 破坏性 | 必须同步改造，否则宕 |

### 各项目雷区

**科研助手（最危险——代码级耦合）**
- L3：换嵌入模型 / 改 SQLite schema / 改 `runtime/` 目录结构 / 改 `config.yaml` 键名 / **改 tool schema 却不同步 `_PRESENTATION`**（启动时直接 `RuntimeError`，服务起不来）
- L2：改 `graph_builder.py` 节点或路由 / 改工具名参数 / 改 SSE 事件格式
- L0：前端样式、`pdf_reader` 内部优化、新增工具且已同步契约

**知识库桌面端（HTTP 契约，中等）**
- L3：改服务端口或路由前缀 / 改 Vault 写入目录规则 / 改敏感目录语义
- L0：UI、Electron 升级、Wiki 模板
- ⚠️ 刚完成"剥离 Obsidian 插件端"的大重构，**仍在收敛期**，近期变更频率偏高 → 桥接先只接只读检索

**PPT Agent（CLI + JSON，最松）**
- L3：改 `DeckSpec.version` 或核心字段 / 改 CLI 子命令名
- L1：升级 `pptxgenjs`（项目里有 `image-size` override，必须重跑烟雾测试）

## 8. 防腐七条（按优先级）

1. **适配器层**——工作台绝不直接消费三方格式，全部经适配器转成本契约
2. **契约测试放在工作台侧**——三方一改，红灯亮在**你自己的仓库**，而不是运行时才炸（本仓库 28 项测试即为此存在）
3. **版本化 + 显式校验**——`DeckSpec.version` 强制校验；知识库 HTTP 加 `/v1`
4. **Pin 版本**——⚠️ **PPTAgent 目前不是 git 仓库，只有 `PPTagent.zip` 备份。开工第一步 `git init` 建基线，否则改造无回滚点**；知识库 pin 到稳定 commit
5. **单侧写入**（§4）
6. **预算与限流上提底座**——这也是模型网关要第一个抽的原因
7. **schema 生成物纳入 CI diff 检查**——`scripts/gen_schema.py` 产生 diff 即失败，防止 TS 镜像漂移

## 9. 安全底线

监听 `127.0.0.1` **不等于安全**：同机浏览器仍可能通过 DNS rebinding 打到服务。
因此必须有：

- `API_AUTH_TOKEN`（本机配置文件，非代码）
- **Origin / Host 校验**（已列入阶段 1 立即改动项）
- 不建 OAuth / JWT —— 单用户本机场景，那是纯负担

---

## 附：本阶段已交付

| 产物 | 路径 |
|---|---|
| Python 契约（单一事实来源） | `contracts/python/workstation_contracts/` |
| JSON Schema（8 份） | `contracts/schema/` |
| TS 类型镜像 | `contracts/ts/src/index.ts` |
| Schema 生成器 | `scripts/gen_schema.py` |
| 契约测试（28 项） | `contracts/python/tests/test_contracts.py` |
| 配置样例 | `config/workstation.example.yaml` |
