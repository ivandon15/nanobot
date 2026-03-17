# Upstream (HKUDS/nanobot) 更新总结

> 基于 `upstream/main` vs 本地 `main` 的 diff，共约 407 个 commit。
> 本文聚焦于**可能值得合并到我们自己 nanobot 的更新**，按主题分类。

---

## 1. 核心 Agent / Loop 改进

| 更新 | 说明 |
|------|------|
| **后台内存整合** | `perf: background post-response memory consolidation` — 回复后异步整合记忆，减少响应延迟 |
| **Token-based 上下文压缩** | 替换原来的消息数量压缩，改用 token 计数，更精准控制上下文窗口 |
| **LLM 重试 + 指数退避** | `feat: add LLM retry with exponential backoff for transient errors` — 瞬时错误自动重试 |
| **截断历史一致性修复** | `fix: keep truncated session history tool-call consistent` + `Fix orphan tool results` — 防止截断后出现孤立 tool result |
| **异常捕获防崩溃** | `fix: add exception handling to prevent agent loop crash` |
| **结构化 post-run 评估** | `refactor: replace <SILENT_OK> with structured post-run evaluation` |
| **版本 ID 写入日志** | `qol: add version id to logging` |

---

## 2. 安全性

| 更新 | 说明 |
|------|------|
| **SSRF 防护** | `security: add SSRF protection, untrusted content marking, and internal URL blocking` — 阻止 agent 访问内网 URL |
| **allowlist bypass 修复** | `fix(auth): prevent allowlist bypass via sender_id token splitting` |
| **ReadFileTool 大小限制** | `fix: add size limit to ReadFileTool to prevent OOM` |

---

## 3. 工具层改进

| 更新 | 说明 |
|------|------|
| **工具参数自动类型转换** | `feat: auto casting tool params to match schema type` — schema 类型不匹配时自动 cast |
| **validate_params 防御** | `fix: guard validate_params against non-dict input` |
| **CronTool 日期解析修复** | `fix: handle invalid ISO datetime in CronTool gracefully` |
| **MCP SSE 传输支持** | `feat(mcp): add SSE transport support with auto-detection` |
| **MCP enabledTools 语义澄清** | `fix(mcp): clarify enabledTools filtering semantics` + 支持注册 MCP 时指定 tool |
| **Shell 工具改进** | 分页、fallback 匹配、更智能输出；`shutil.which()` 替代 `shell=True` |
| **Filesystem 工具改进** | 分页、fallback 匹配 |
| **Web 搜索多 provider** | `feat(web): multi-provider web search + Jina Reader fetch`，可配置 fallback |
| **图片 MIME 从 magic bytes 检测** | `fix(context): detect image MIME type from magic bytes instead of file extension` |
| **非视觉模型过滤 image_url** | `fix: filter image_url for non-vision models at provider layer` |
| **image_url 被拒时重试** | `fix: handle image_url rejection by retrying without images` |

---

## 4. Provider / LLM 支持

| 更新 | 说明 |
|------|------|
| **Ollama 本地模型** | `feat: add Ollama as a local LLM provider` |
| **Azure OpenAI** | `Support Azure OpenAI` + 消息 sanitize + temperature 处理 |
| **OpenRouter 修复** | 多个 fix，防止 model 名称双重前缀 |
| **Codex reasoning_effort** | `fix(codex): pass reasoning_effort to Codex API` |
| **Alibaba Cloud Coding Plan** | `feat: Add Alibaba Cloud Coding Plan API support` |
| **VolcEngine/BytePlus coding plan** | 命名规范化 + 文档 |
| **LangSmith 集成** | `Integrate Langsmith for conversation tracking` |
| **多 choices tool_calls 合并** | `fix: merge tool_calls from multiple choices in LiteLLM response` |
| **Gemini tool call metadata 保留** | `fix: preserve provider-specific tool call metadata for Gemini` |
| **reasoning_content 跨 turn 保留** | subagent 和主 loop 中保留 reasoning 字段 |

---

## 5. Channel 改进

### Telegram
- 流式草稿/进度消息 (`feat: Implement Telegram draft/progress messages`)
- Group topic 支持
- Group mention policy 可配置
- 代理崩溃修复
- 文件扩展名保留
- `/stop` 和 `/restart` 命令

### Feishu (飞书)
- 消息回复/引用支持
- 工具调用以代码块卡片展示
- 多表格时自动分割卡片消息
- 音频转录 (Groq Whisper)
- 音频文件 `.opus` 扩展名修复
- Reactions/消息已读/p2p 事件处理
- Group mention policy 简化
- lark ws Client 事件循环隔离

### DingTalk (钉钉)
- 群聊消息支持
- 语音识别文本 fallback
- 文件/图片/富文本消息接收
- 下载文件保存到 media dir 而非 /tmp

### Discord
- Group policy 控制群组响应行为
- 附件回复 fallback

### QQ
- 群 @ 消息支持
- Markdown payload 发送
- 可配置消息格式 + onboard backfill

### WeCom (企业微信)
- 新增 WeCom channel (WebSocket SDK)

### WhatsApp
- 媒体消息支持（图片/文件）
- 避免丢弃纯媒体消息

### Slack
- 空文本响应处理
- thread_ts 直消息跳过

---

## 6. 架构 / 基础设施

| 更新 | 说明 |
|------|------|
| **Channel 插件架构** | `feat: channel plugin architecture with decoupled configs` — channel 配置解耦，支持 pkgutil 自动发现 |
| **多实例支持** | `feat: multi-instance support with --config parameter` + gateway `--workspace/--config` |
| **`/restart` 命令** | 从任意 channel 重启 bot 进程 |
| **SIGTERM/SIGHUP 处理** | 优雅退出 |
| **自定义 provider session affinity** | `x-session-affinity` header 用于 prompt caching |
| **Heartbeat 注入当前时间** | Phase 1 prompt 中注入 datetime |
| **抑制不必要的 heartbeat/cron 通知** | 减少噪音 |

---

## 7. CLI 改进

| 更新 | 说明 |
|------|------|
| **Spinner 在打印进度前暂停** | 避免输出混乱 |
| **`--workspace` 和 `--config` flags** | agent 子命令支持 |
| **Windows 兼容性** | signal handler、`-m nanobot` 启动、npm 调用 |

---

## 建议优先关注的更新

以下更新与我们自己的改动关联度高，或通用价值大，建议优先评估：

1. **SSRF 防护** — 安全性，直接可用
2. **LLM 重试 + 指数退避** — 稳定性提升
3. **工具参数自动类型转换** — 减少 agent 出错
4. **后台内存整合** — 性能优化
5. **MCP SSE 传输支持** — 扩展 MCP 兼容性
6. **非视觉模型过滤 image_url** — 防止 provider 报错
7. **ReadFileTool 大小限制** — 防 OOM
8. **Token-based 上下文压缩** — 更精准的上下文管理
9. **Channel 插件架构** — 如果我们有自定义 channel，值得参考

---

*生成时间：2026-03-17，基于 upstream/main @ 84565d7*
