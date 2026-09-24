"""内部工具层：同语言技能直接 import（LocalExecutor），跨语言走 CLI 子进程。

PDF 解析是"补齐"而非"复用"（知识库根本没有、PPTAgent 不涉及），故走
CLI 工具化，不做 MCP（阶段 11 结论）。
"""
