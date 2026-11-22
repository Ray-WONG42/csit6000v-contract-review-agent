# Chat context management / 聊天上下文管理

Each planner step packs a fresh, bounded request. It does not append complete transcripts
and tool results indefinitely. The current user request and tool validation schemas stay intact.

- Historical dialogue is represented by recent turns and extractive, query-related excerpts,
  with referenced record IDs retained. Compression is marked explicitly.
- Equivalent contract reads share a cached observation. Duplicate observations do not consume
  the prompt repeatedly. Pagination metadata remains outside the text excerpt.
- Contract reads return a section outline, source paragraph IDs, coverage and a continuation
  cursor. Query-based local semantic/BM25 retrieval selects relevant source paragraphs.
  To continue the ranked read, pass both `next_start` and `selection_query` as `start` and `query`.
- Tool schemas omit presentation titles/defaults, while preserving property names, types,
  required fields, enums and access validation. JSON uses compact separators.
- Before any SDK submission, the packer reserves response space and serialization overhead.
  It compresses history and observation excerpts to fit the selected backend's existing budget.
  It never silently truncates the current user request or increases the course context limit.
- Complete observations remain in the private task checkpoints; local chat history stays in
  the browser. A shortened model excerpt must not be described as a full document read.
- Requests whose indispensable content still cannot fit fail locally with a context-specific
  explanation. A remote context error is also reported as a context error, not a network outage.

实现位置：`app/assistant.py` 的 `fit_chat_payload`、`observation_context`、
`compact_schema` 及分页 `read_contract`。工具实际执行时仍会校验账号权限和完整参数。
压缩是保留出处的节选，不使用额外模型编造摘要；被压缩的内容仍可通过合同工具继续读取。
当前问题不会被静默删减。完整原文、历史、授权及运行检查点不会进入公开发布包。
