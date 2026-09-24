# 06 · knowledge-bridge/1（知识库只读检索契约）

阶段 3 把「知识环」（`knowledge-loop-desktop`）接成底座的一个只读技能。契约面
是 `knowledge-bridge/1`：JSON in → JSON out，与 `ppt-bridge/1` 完全对称。

## 形态选择

- 知识库**没有 HTTP 服务**（`core/` 零 electron 依赖），纯 Node 即可跑。
- 因此用一次性 **CLI 子进程**（不是长驻 HTTP），复用底座的 `SubprocessExecutor`。
- 知识库仓库没有 `tsx`/`zod`，CLI 用其既有 `esbuild` devDep 打包 `core/`
  （`npm run bridge`），两侧契约校验：TS 侧手写 strict 校验，Python 侧
  pydantic `extra="forbid"`。
- 将来知识库若加长驻 HTTP 服务，只需把执行器从 `Subprocess` 切到 `Http`——
  这正是 `Executor` 抽象的价值，技能代码不变。

## 请求（request.json）

```jsonc
{
  "apiVersion": "knowledge-bridge/1",   // 必须，否则 BAD_API_VERSION
  "mode": "search",                      // 现阶段只支持 search
  "vault": "D:/abs/path/to/vault",       // 必须，绝对路径（Windows 原生，勿传 /d/...）
  "query": "检索词",                     // 必须，非空
  "limit": 8,                            // 可选，正整数，默认 8
  "scope": null                         // 可选，["folder"] 限制检索范围；null = 全库
}
```

## 响应（response.json）

成功与失败都是响应（退出码始终 0，调用方从响应判断）：

```jsonc
{
  "apiVersion": "knowledge-bridge/1",
  "ok": true,
  "errorCode": null,
  "error": null,
  "indexedFiles": 12,
  "skippedFiles": 0,
  "chunkCount": 340,
  "elapsedMs": 106,
  "results": [
    {
      "score": 123,
      "excerpt": "…片段…",
      "chunk": {
        "content": "…",
        "heading": "标题",
        "headingPath": ["父", "子"],
        "startLine": 10,
        "endLine": 17,
        "source": {
          "type": "note",                 // note|pdf|image|web|conversation
          "pathOrUrl": "topics/foo.md",    // 知识库内相对路径
          "locator": "heading=...&chunk=1",
          "contentHash": "41d8cd01",       // 解析器产出的短哈希，作去重盐
          "parserVersion": "markdown-v1"
        }
      }
    }
  ]
}
```

失败：`{ "ok": false, "errorCode": "INDEX_FAILED" | "MISSING_VAULT" | ..., "error": "..." }`。

## 适配器映射（KB SourceRef → 底座 SourceRef）

| KB `source.type` | 底座 `SourceType` |
|---|---|
| `note` | `note` |
| `pdf` | `attachment` |
| `image` | `attachment` |
| `web` | `web` |
| `conversation` | `generated` |

- `source_id` 统一由 `derive_source_id(pathOrUrl, contentHash)` 生成，保证同文件
  同字节幂等、便于引用回填。
- `locator` 映射进底座 `Locator{section=heading, line_start, line_end}`；KB 原始
  `locator` 串存入 `meta.kb_locator`。
- 知识库检索**只读**：技能 manifest 只声明 `subprocess` + `fs:derived`，**不**
  声明 `fs:primary` / `llm` / `net`。Vault 唯一写入通道仍归知识库桌面端。

## 双向契约测试

- 知识库侧改了请求形状 → `parseRequest` 立刻抛 `CONTRACT_DRIFT`（vitest 覆盖）。
- 工作台侧改了响应形状 → `BridgeResponse.model_validate` 立刻抛
  `KNOWLEDGE_BRIDGE_CONTRACT_DRIFT`（pytest 覆盖）。
- 任一侧新增字段，另一侧红灯，不会发生静默漂移。
