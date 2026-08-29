# pi_deepagent × Pi Coding Agent Roadmap

文件狀態：Proposed  
建立日期：2026-08-29  
產品範圍：Agent loop、LLM provider、模型切換、檔案編輯、Subagents、Session 保存、Compaction、Slash commands、Context files、Skills、Extensions  
明確不在本期範圍：OAuth / ChatGPT 訂閱登入

## 1. 產品目標

將目前的 pi_deepagent 從「可執行工具呼叫的 DeepAgents factory」演進成一個具備 Pi 核心工作流的 Python coding agent runtime：

1. 使用者可以在同一個 session 中持續工作，離開後可恢復、分支與壓縮上下文。
2. Provider、模型、工具、context、skill 與 extension 都透過穩定抽象層組合，而不是硬編碼在 CLI。
3. 保留現有的 Python API、workspace-bound tools 與 LangChain / DeepAgents 相容性。
4. 第三方開發者可以只依賴公開 protocol/API，新增工具、命令、provider、compactor 或 UI adapter，不需修改核心。
5. 預設安全；需要主機級 shell 或第三方 extension 時，必須是明確且可追蹤的選擇。

這份文件是功能路線圖與開發 backlog，不代表所有功能一次完成，也不要求複製 Pi 的 TypeScript 內部實作。目標是比照 Pi 的使用者能力與可擴充設計邏輯。

## 2. 目前基線與 Pi 對照

### 2.1 已存在的能力

| 區域 | 目前狀態 | 主要位置 |
|---|---|---|
| Agent 建立 | 可指定 LangChain model、workspace、shell backend、額外 tools、subagents | [agent.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/agent.py:12) |
| 檔案工具 | pi_read、pi_write、pi_edit、pi_bash；路徑會限制在 workspace | [tools.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/tools.py:17) |
| DeepAgents 工具 | 依 backend 提供 filesystem tools、execute 與 task 能力 | [agent.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/agent.py:52) |
| CLI | 一次性 prompt 與記憶體內 REPL history | [cli.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/cli.py:34) |
| Prompt | 固定 system prompt，描述目前可用工具 | [prompt.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/prompt.py:8) |
| 測試 | 目前已有 12 個 unittest，基礎工具與 factory 行為已覆蓋 | tests/ |

### 2.2 主要差距

Pi 的 coding-agent package 已將 agent loop、model runtime、session manager、context/resource loader、TUI 與 extension runtime 分層；目前 repo 還把大部分責任集中在 factory、CLI 與固定 prompt。

目前 create_pi_deepagent 只向 DeepAgents 傳入 model、tools、system_prompt、backend、subagents。DeepAgents 雖然提供 memory、skills、permissions、interrupt、checkpointer 等延伸參數，但目前 wrapper 沒有對外暴露，不能視為已完成的產品功能。

Pi 的對照基準：

- [Pi Coding Agent README](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/README.md:15)：互動模式、session、commands、skills、extensions、SDK/RPC。
- [Pi 內建工具 registry](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/src/core/tools/index.ts:83)：read、bash、edit、write、grep、find、ls。
- [Pi AgentSession](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/src/core/agent-session.ts:306)：prompt、abort、model、compact、tree navigation。
- [Pi ModelRuntime](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/src/core/model-runtime.ts:112)：provider/model/auth runtime。
- [Pi ResourceLoader](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/src/core/resource-loader.ts:71)：context files、skills、prompt templates、themes。

## 3. 產品原則

### P-01：Core 小而穩定，能力由 extension 增長

核心只負責 runtime、session、工具與公開 protocol。特殊工作流、第三方服務、客製 UI 應使用 extension，不把每一種需求硬塞進 CLI。

### P-02：CLI、REPL、SDK 共用同一套 application service

Slash command 不應直接操作 DeepAgents graph 的內部 state；CLI、未來 TUI、RPC、Python SDK 都呼叫同一組 service。

### P-03：公開 contract 優先於實作細節

第三方只依賴 Protocol、dataclass、event schema 與 capability interface。DeepAgents、SQLite、LangChain 都應是可替換的 adapter。

### P-04：所有長期狀態顯式保存

Session、branch、compaction、tool result、model selection 與 extension state 都要有明確 schema 或 metadata，不依靠程序記憶體中的 list。

### P-05：安全預設

預設使用 workspace filesystem backend；主機 shell、project-local extensions 與外部儲存都要有明確設定、trust 或 permission policy。

### P-06：可測試、可觀測、可恢復

每一個 agent turn 都必須能被測試 fixture 重播，工具呼叫與模型錯誤要有可辨識的 event，失敗後能從 session 繼續，而不是只能重開程序。

## 4. 目標架構

    CLI / REPL / future TUI / SDK / RPC
                    │
            Application Services
                    │
                 AgentRuntime
           ┌────────┼────────┐
       AgentLoop  Model     ToolRegistry
           │       │             │
       EventBus  Provider     Tool policy
           │       │             │
     SessionStore  ContextLoader  ExtensionManager
           │
     SQLite default / external adapter

建議的依賴方向：

- AgentLoop 只依賴 Model、ToolRegistry、SessionStore、ContextLoader 與 event interfaces。
- CLI 只依賴 Application Services，不直接拼裝 message history。
- ExtensionManager 只透過 ExtensionContext 暴露能力，不把核心物件的 mutable internals 交給 extension。
- DeepAgents 與 LangChain 由 adapter 包裝；未來可以替換 graph、model 或儲存層而不改上層 API。

## 5. Milestone 總覽

優先級定義：

- P0：完成基本 Pi-style coding workflow 的必要能力。
- P1：完成日常使用與第三方擴充所需的 parity。
- P2：生態系與進階整合，第一版可延後。

| Milestone | 目標 | 優先級 | 主要依賴 | Exit criteria |
|---|---|---:|---|---|
| M0 | Core contracts 與分層骨架 | P0 | 無 | 新舊 API 邊界明確，contract tests 可執行 |
| M1 | Agent loop 與 runtime events | P0 | M0 | 支援串流、取消、queue、retry，保留同步相容 API |
| M2 | LLM provider registry 與模型切換 | P0 | M0、M1 | 可列出、解析、切換模型；不需重啟 session |
| M3 | 工具 registry 與檔案編輯 parity | P0 | M0、M1 | 工具可註冊、控權限、回報 diff；workspace 安全測試完整 |
| M4 | Session persistence | P0 | M0、M1 | SQLite 可恢復、分支；外部 store 有可實作 protocol |
| M5 | Compaction | P0 | M1、M2、M4 | 自動/手動壓縮，完整歷史仍可回看 |
| M6 | Slash commands | P1 | M1、M2、M4 | 內建 commands 與 CLI/SDK 共用 parser/service |
| M7 | Context files | P1 | M0、M4、M6 | 探索、排序、reload、停用與 trust policy 完整 |
| M8 | Skills | P1 | M7、M9 | Agent Skills 格式可發現、載入、明確呼叫 |
| M9 | Extensions abstraction | P0 architecture / P1 ecosystem | M0、M1、M2、M3、M6、M7 | 第三方可不改核心新增 tool/command/provider/event handler |
| M10 | Subagents | P1 | M1、M3、M4、M9 | 子 agent 可執行、取消、保存 parent-child 關係與結果 |

實作順序不必完全依照編號：M9 的 extension contract 要在 M8 前先定義，M10 則應等 runtime、session 與 extension lifecycle 穩定後再做。

## 6. Milestone 詳細 backlog

### M0 — Core contracts 與分層骨架

目標：把目前集中在 factory/CLI 的責任拆成可替換的公開邊界，同時維持既有使用方式。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| CORE-001 | P0 | 定義 AgentRun、AgentEvent、ToolCall、ModelRef、SessionId、RunResult 等 domain types | 型別與序列化格式有文件；不依賴 LangChain message class |
| CORE-002 | P0 | 建立 runtime、providers、tools、sessions、context、extensions、commands 模組邊界 | create_pi_deepagent 仍可工作；CLI 不再自行管理核心狀態 |
| CORE-003 | P0 | 定義 capability 與 error taxonomy | provider、tool、session、extension 錯誤可區分；CLI 可顯示可理解訊息 |
| CORE-004 | P0 | 建立 fake model、fake tool、in-memory store 的 contract test fixtures | 所有核心測試不需網路、不需真實 API key |
| CORE-005 | P0 | 定義相容性政策與 public API 清單 | 既有 create_pi_deepagent、extra_tools、subagents 的行為有 regression test |

Exit criteria：

- 既有 12 個測試保持通過。
- 新模組可以用 fake adapter 驗證，不必啟動真實模型。
- 所有後續 milestone 都能引用穩定的 protocol，而不是引用 DeepAgents 私有結構。

### M1 — Agent loop 與 runtime events

目標：對照 Pi 的 agent loop 與 AgentSession，提供串流、可取消與可恢復的 turn lifecycle。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| LOOP-001 | P0 | 實作 AgentLoop.run 與 AgentLoop.stream 的 async-first interface | 同一個 request 可取得 final result 或逐筆 event；同步 invoke 由 adapter 保留 |
| LOOP-002 | P0 | 將 DeepAgents/LangGraph stream 正規化成公開 events | 至少涵蓋 run_start、model_start、model_delta、tool_start、tool_result、run_end、error |
| LOOP-003 | P0 | 實作 steering、follow-up、abort 與 queued messages | agent 工作中可插入 steering；abort 後 queue 可恢復或明確丟棄 |
| LOOP-004 | P0 | 實作 retry policy 與錯誤分類 | transient provider error 可 bounded retry；工具副作用不重複執行 |
| LOOP-005 | P1 | 將 run metadata 放入 context | 每次 run 可取得 session id、model、workspace、reasoning level 與 parent run |
| LOOP-006 | P1 | 建立事件 recorder 與測試 replay | 可用固定 event fixture 重播 CLI 顯示與 session append |

Exit criteria：

- REPL 不必等待整個 invoke 才能顯示 tool/model 進度。
- Ctrl-C/取消不會破壞 session 狀態。
- 事件 schema 足以支援未來 TUI、RPC 與 extension handler。

### M2 — LLM provider registry 與模型切換

目標：比照 Pi 的 provider/model runtime，把 model 字串解析、模型能力與 credential resolver 從 CLI/factory 抽離。

OAuth 不在本 milestone；credential resolver 先支援環境變數、設定檔與已注入 model，OAuth 另立後續 milestone。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| MODEL-001 | P0 | 定義 ProviderDescriptor、ModelDescriptor、ModelCapabilities 與 ProviderAdapter | provider、model id、context window、vision、tool calling、thinking 等能力可查詢 |
| MODEL-002 | P0 | 實作 ProviderRegistry 與 model catalog | 可註冊內建 provider、列出模型、處理重複 ID 與停用 provider |
| MODEL-003 | P0 | 實作 ModelSelector parser | 支援 provider/model 與 thinking level；輸入錯誤時提供可修正訊息 |
| MODEL-004 | P0 | 實作 CredentialResolver | 順序與來源明確；不得把 secret 寫入 session、event 或一般 log |
| MODEL-005 | P1 | 實作 runtime model switch | /model 或 API 切換後，下一個 turn 使用新模型，不需重建 session |
| MODEL-006 | P1 | 實作模型能力檢查 | vision、tool calling、context window 不足時，在送出 request 前阻擋並說明原因 |
| MODEL-007 | P1 | 加入 provider/model fake adapter tests | 測試 catalog、selector、switch、credential precedence，不呼叫外部服務 |

Exit criteria：

- CLI、REPL、SDK 都使用同一個 ModelRuntime。
- model selection 不再直接等同於任意 LangChain 字串。
- 未來新增 provider 只需實作 adapter/descriptor，不需修改 AgentLoop。

### M3 — 工具 registry 與檔案編輯 parity

目標：以 Pi 的 read、write、edit、bash、grep、find、ls 為可配置的工具能力，保留目前 workspace-bound 安全邏輯。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| TOOL-001 | P0 | 定義 ToolSpec、ToolContext、ToolResult、ToolPolicy 與 ToolRegistry | tool name、schema、description、read/write/exec capability 可查詢 |
| TOOL-002 | P0 | 將 pi_read/write/edit/bash 遷移至 registry adapter | 舊工具名稱與既有參數可相容；工具可被 allowlist/denylist |
| TOOL-003 | P0 | 補齊 grep、find、ls 的一致介面 | 工具輸出有 bounded size、錯誤格式與 workspace path semantics |
| TOOL-004 | P0 | 強化 edit result | 回傳 replacements、changed path、diff metadata、first changed line；非唯一 match 預設拒絕 |
| TOOL-005 | P0 | 工具執行 policy | default 不允許任意 host shell；timeout、output limit、cwd、cancel 與 exit code 可控 |
| TOOL-006 | P1 | 實作檔案引用與附件輸入 | 支援類似 @file 的初始 prompt；圖片由 model capability 決定是否允許 |
| TOOL-007 | P1 | 建立 symlink、absolute path、root escape、binary、large output 測試 | 所有 path escape 與超限情境都有 deterministic result |

重要安全決策：目前的 LocalShellBackend 是主機級 shell，virtual_mode 不等於 sandbox。M3 完成前，應把它視為明確的 trusted execution mode，不能默認開啟。

Exit criteria：

- 工具可由第三方 registry 注入，不需修改 agent.py。
- 工具結果可被 event、session、compaction 與 UI 共用。
- default filesystem mode 與 trusted shell mode 的風險在文件與 CLI help 中清楚區分。

### M4 — Session persistence

目標：提供 SQLite 預設實作，同時以 protocol 支援外部儲存，對照 Pi 的 JSONL session tree、resume、fork 與 branch。

#### 建議資料模型

核心資料分成兩層：

1. Session metadata：session id、name、workspace、created_at、updated_at、active branch、model snapshot。
2. Append-only entries：user message、assistant message、tool call、tool result、event、compaction record、branch marker。

每筆 entry 至少要有 entry id、session id、parent entry id、kind、payload version、created_at。LangChain 物件不能直接當作永久 schema。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| SESSION-001 | P0 | 定義 SessionStore protocol | 支援 create、load、append、list、resume、fork、branch、delete/close；所有方法有 version/concurrency 語意 |
| SESSION-002 | P0 | 實作 SQLiteSessionStore | 預設可建立 schema、transaction、WAL、索引與 migration；程序重啟後可恢復 |
| SESSION-003 | P0 | 實作 append-only entry serializer | message、tool call/result、event 可 round-trip；secret 與不可序列化物件不落盤 |
| SESSION-004 | P0 | 實作 resume/new/fork/tree domain service | 可從指定 entry 建立新 branch；原 branch history 不被覆寫 |
| SESSION-005 | P1 | 實作 session listing 與 metadata | 可依 workspace、name、updated_at 列出；可顯示 message/token/cost 等可得統計 |
| SESSION-006 | P1 | 定義 ExternalSessionStore adapter contract | 外部 DB、HTTP、object storage 只需實作 protocol；支援 optimistic version 或 conflict error |
| SESSION-007 | P1 | 實作 JSONL import/export | 可將 SQLite session 匯出/匯入；格式版本化，錯誤資料不會靜默遺失 |
| SESSION-008 | P1 | 實作 --no-session 與 storage 設定 | 可明確選 ephemeral；資料庫位置可由設定或環境變數指定 |
| SESSION-009 | P0 | 測試 crash/restart/concurrent append | 中斷後不產生半筆 entry；衝突可偵測，不會默默覆蓋他人 session |

預設建議：SQLite 放在 user-level data directory，project metadata 與 session content 分離；透過 SessionStore protocol 支援外部服務，不把 SQLite schema 直接暴露給上層。

Exit criteria：

- 使用者可以離開程序後 resume。
- fork/branch 不需要複製整個 prompt history。
- Session store 可替換而 AgentLoop、CLI、compaction 不需改寫。

### M5 — Compaction

目標：對照 Pi 的手動與自動 compaction，在 context 接近上限或 overflow 時可恢復工作，同時保留完整歷史。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| COMPACT-001 | P0 | 定義 ContextBudget 與 UsageEstimator | 依 model descriptor 計算 input/output/tool/context usage；估算不準時標示來源 |
| COMPACT-002 | P0 | 定義 CompactionStrategy protocol | 可替換 model summarizer、deterministic truncation、custom extension strategy |
| COMPACT-003 | P0 | 實作保留規則 | 保留 system/context、最近 user/assistant/tool path、未完成 run 與 branch metadata |
| COMPACT-004 | P0 | 實作 automatic trigger | 接近 threshold proactive compact；provider 回報 overflow 時 compact 後 bounded retry |
| COMPACT-005 | P1 | 實作 /compact [instructions] | 可手動提供摘要指示；結果寫入 session compaction record |
| COMPACT-006 | P1 | 保留完整歷史與可回看 | compact 只改下一次 model context，不刪除原始 entries；tree/fork 可回到壓縮前 |
| COMPACT-007 | P1 | 加入摘要失敗 fallback | summarizer 失敗時 deterministic trim 或明確中止，不能產生空白 context |
| COMPACT-008 | P1 | 測試 token boundary、branch、tool output | 不同 context window 與長工具輸出下結果可預期 |

Exit criteria：

- 長 session 可繼續執行，不必手動重開。
- 使用者知道何時發生 compaction，且能從 session history 追溯。
- compaction policy 可由 extension 替換。

### M6 — Slash commands

目標：建立可被 REPL、未來 TUI、SDK 與 extension 共用的 command registry。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| CMD-001 | P1 | 定義 CommandSpec、CommandContext、CommandResult | command 有 name、aliases、usage、description、handler、interactive policy |
| CMD-002 | P1 | 實作 parser 與 dispatch | 支援 command name、quoted args、空白、未知 command、錯誤 usage |
| CMD-003 | P1 | 實作第一批內建 commands | /model、/new、/resume、/session、/compact、/reload、/quit |
| CMD-004 | P1 | 實作 session commands | /name、/fork、/tree、/export、/import |
| CMD-005 | P1 | 統一 command 與普通 prompt 的邊界 | 內建 command 不會誤送給 model；command output 可產生 notification event |
| CMD-006 | P1 | 提供 extension command namespace | 第三方 command 可以註冊、覆寫政策明確、名稱衝突在 startup 失敗或可診斷 |
| CMD-007 | P2 | 完成 command completion/help | 輸入 / 可列出 commands、usage 與可用參數 |

Exit criteria：

- CLI 不再把 /exit 當成特別硬編碼例外。
- 新增 command 不需修改主迴圈。
- 同一 command service 可供未來 RPC client 呼叫。

### M7 — Context files

目標：對照 Pi 的 global/parent/current context file 探索與 system prompt layering，讓專案規則可被一致載入。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| CONTEXT-001 | P1 | 定義 ContextSource、ContextDocument、ContextBundle | 每個來源保留 path、scope、precedence、loaded_at、content hash |
| CONTEXT-002 | P1 | 實作 context discovery | 支援 user/global、cwd parent chain、current directory 的 AGENTS.md 與 CLAUDE.md |
| CONTEXT-003 | P1 | 實作 override 規則 | AGENTS.override.md 可取代同目錄 AGENTS/CLAUDE；不同目錄內容仍依序合併 |
| CONTEXT-004 | P1 | 實作 SYSTEM.md 與 APPEND_SYSTEM.md | 可取代或追加 system prompt；CLI 有 disable/override 方式 |
| CONTEXT-005 | P1 | 實作 context precedence 與大小限制 | user/project/CLI/skill/extension 層次明確；超大檔案有 bounded behavior |
| CONTEXT-006 | P1 | 實作 /reload | reload context、skills、prompts、extensions 時，session history 不被清空 |
| CONTEXT-007 | P1 | 加入 trust policy interface | project-local resources 可設定 ask/always/never；未 trust 前不執行 project extension |
| CONTEXT-008 | P1 | 加入 provenance 與安全測試 | startup 可列出載入來源；path traversal、衝突、惡意 symlink 有測試 |

Exit criteria：

- agent 每次 run 的 system context 可重現。
- 使用者可以知道規則來自哪一個檔案。
- project-local code/resource 不會在沒有 policy 的情況下靜默執行。

### M8 — Skills

目標：支援 Agent Skills 風格的 on-demand capability package，並與 context、commands、extensions 共存。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| SKILL-001 | P1 | 定義 SkillManifest 與 SkillLoader | 以 SKILL.md metadata 解析 name、description、version、入口檔 |
| SKILL-002 | P1 | 實作 discovery paths | 支援 user/global、parent、project 的 .agents/skills 與 .pi/skills |
| SKILL-003 | P1 | 實作 explicit invocation | /skill:name 可載入指定 skill；不存在、衝突、停用時有清楚錯誤 |
| SKILL-004 | P1 | 實作 bounded loading policy | 先載入 metadata，僅在需要時載入全文；限制檔案大小與遞迴深度 |
| SKILL-005 | P1 | 實作 automatic selection hook | 保留由 runtime/extension/agent 決定是否載入的 interface，不把自動選擇硬編碼在 CLI |
| SKILL-006 | P1 | 實作 enable/disable 與 provenance | settings/CLI 可停用 discovery；startup 可顯示已發現與已載入 skills |
| SKILL-007 | P1 | 加入 skill conflict/trust tests | 同名 skill 有 deterministic precedence；project skill 遵守 trust policy |

Exit criteria：

- 第三方可只提供 SKILL.md，不修改核心即可提供工作流程。
- skill instruction 不會在沒有來源紀錄時默默注入 system context。
- extension 可透過公開 loader 取得 skill metadata。

### M9 — Extensions abstraction

目標：比照 Pi 的設計邏輯，建立 Python-first、UI-neutral、可替換的 extension API。這是本 roadmap 的架構核心。

#### 9.1 建議公開抽象層

Extension 不應直接拿到 DeepAgents graph、SQLite connection 或 CLI input loop。核心提供 ExtensionContext，依 capability 暴露服務：

| Capability | 用途 |
|---|---|
| ToolRegistry | 註冊或替換 tools |
| CommandRegistry | 註冊 slash commands |
| EventBus | 監聽 agent/session/tool/model/compaction events |
| ProviderRegistry | 新增 provider、model catalog、model adapter |
| CompactionRegistry | 新增 summarizer 或 compaction policy |
| Context/Skill loader | 增加 resource source 或載入規則 |
| SessionStore factory | 提供外部 session backend |
| UI adapter | 提供 notification、widget、status data，不依賴特定 TUI |
| Config service | 讀取已驗證的 extension settings，不直接讀任意核心 private state |

#### 9.2 Task backlog

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| EXT-001 | P0 | 定義 ExtensionManifest、ExtensionContext、ExtensionHandle 與 API version | extension 可宣告 id、version、capabilities、dependencies；API version 不相容時拒絕載入 |
| EXT-002 | P0 | 定義 lifecycle | activate、deactivate、dispose、reload；支援同步與 async factory；啟動失敗可隔離 |
| EXT-003 | P0 | 將 tool/command/event/provider/compactor 接成 registries | extension 只透過 registry/context 操作；核心不需 import 第三方模組 |
| EXT-004 | P1 | 實作 local file discovery | 支援 user-level 與 project-level Python extension；順序、命名、停用規則文件化 |
| EXT-005 | P1 | 實作 installed package discovery | 支援 Python entry point，例如 pi_deepagent.extensions；不把 npm/TypeScript runtime 當作必要依賴 |
| EXT-006 | P1 | 實作 trust/permission boundary | project extension 需 trust；extension 的 host access、network、subprocess capability 可被宣告與拒絕 |
| EXT-007 | P1 | 實作 extension event hooks | 至少支援 agent_start/end、before/after_model、tool_call/result、session、compaction、error |
| EXT-008 | P1 | 實作 reload 與 teardown | reload 後不重複註冊 handler/tool；舊 handle 可釋放資源 |
| EXT-009 | P1 | 提供 reference extensions | 至少一個 custom command、一個 tool、一個 provider adapter 或 session adapter |
| EXT-010 | P1 | 建立 extension contract test kit | 第三方可用 test helper 驗證 manifest、lifecycle、events、capability policy |
| EXT-011 | P2 | 提供 extension package metadata/config UI | 可列出、啟用、停用、檢查版本與來源；不在核心中實作 package manager |

#### 9.3 Extension API 設計約束

- 公開的是 protocol 與 capability，不是 DeepAgents/LangChain 私有物件。
- handler 應可取得 cancellation token、structured logger 與 run/session metadata。
- event handler 的錯誤預設不能讓整個 agent run 靜默失敗；依 hook policy 決定 fail-open 或 fail-closed。
- tool/command/provider id 必須 namespace 化，避免第三方互相覆寫。
- extension 的 arbitrary Python code 等同本機程式碼，必須有 trust warning；API abstraction 不是 sandbox。
- UI capability 只發出語意資料與 notification，核心不能假設一定存在 terminal UI。

#### 9.4 Extension exit criteria

一個第三方 package 能在不修改 pi_deepagent 核心程式的情況下：

1. 註冊一個新 slash command。
2. 註冊一個帶 schema 的 tool。
3. 監聽 tool_result 並產生 notification。
4. 選擇性註冊 provider/model 或 custom compaction strategy。
5. 被 reload、停用、測試與診斷。

### M10 — Subagents

目標：在 AgentLoop、SessionStore 與 Extension lifecycle 穩定後，將 DeepAgents 的 subagents 能力變成可管理、可觀測、可保存的產品功能。

Pi core 刻意不內建 subagents；本 repo 已經有 DeepAgents subagents 入口，因此本 milestone 的重點不是重新發明 agent，而是建立一致的 task lifecycle、policy 與 session model。

| Task ID | 優先級 | Task | 驗收條件 |
|---|---:|---|---|
| SUBAGENT-001 | P1 | 定義 SubagentSpec | 可指定 name、prompt、model、tools、context、max depth、timeout、budget |
| SUBAGENT-002 | P1 | 定義 parent-child run/session linkage | 子 agent 的 run、tool、結果可追溯到 parent session/entry |
| SUBAGENT-003 | P1 | 實作 task lifecycle events | dispatch、started、progress、completed、failed、cancelled 都有 event |
| SUBAGENT-004 | P1 | 實作 concurrency/depth/budget policy | 同時子 agent 數量、遞迴深度、token/time budget 可限制 |
| SUBAGENT-005 | P1 | 實作取消與錯誤傳遞 | parent abort 時子 agent 可取消；單一 child failure 不會默默吞掉 |
| SUBAGENT-006 | P1 | 將 child result 寫入 session | 可在 session/tree 中看到 child summary、artifact、error 與耗時 |
| SUBAGENT-007 | P2 | 提供 command/UI presentation | 可顯示 task list、進度與結果；presentation 不耦合 AgentLoop |

Exit criteria：

- task 不是只有 DeepAgents graph 裡的一個隱藏工具，而是可被 policy、session、event、extension 觀察的 domain operation。
- 使用者可限制 subagent 讀寫 workspace 與可用 provider。
- parent session 可在重啟後看到已完成或失敗的 child tasks。

## 7. 依賴關係與建議交付順序

### 7.1 Critical path

    M0 → M1 → M4 → M5
     │    ├→ M2
     │    └→ M3
     └──────────────→ M6 → M9 → M8
                            └→ M10

較精確的依賴：

- M1 依賴 M0。
- M2 依賴 M0，模型切換整合再依賴 M1。
- M3 依賴 M0；工具事件與取消整合依賴 M1。
- M4 依賴 M0、M1。
- M5 依賴 M1、M2、M4。
- M6 依賴 M1、M2、M4。
- M7 依賴 M0、M4、M6。
- M9 的基本 lifecycle/registry 可在 M1～M3 後開始；完整 discovery/trust 依賴 M6、M7。
- M8 依賴 M7 與 M9，讓 skill 能成為 extension/resource。
- M10 依賴 M1、M3、M4、M9。

### 7.2 建議的第一個實作批次

第一批只建立骨架，不先做 UI：

1. CORE-001～CORE-005
2. LOOP-001、LOOP-002
3. MODEL-001、MODEL-003
4. TOOL-001、TOOL-002
5. 以 fake model + in-memory session 做一條 end-to-end contract test

第一批完成後，才適合並行開 M2/M3/M4；否則 session、commands、extensions 很容易各自定義一套不同的 message/event schema。

## 8. Definition of Done

每個 milestone 要同時滿足：

- 功能：完成該 milestone 的 exit criteria。
- 相容性：既有 API 不被無聲改變；必要 breaking change 要有 migration note。
- 測試：至少有 unit/contract test；涉及 session、stream、tool policy 時要有 integration test。
- 文件：公開 protocol、設定、CLI 使用方式與安全限制有說明。
- 可觀測性：錯誤包含 stable error code；event/session 可追蹤 run。
- 安全性：secret 不進 prompt log/session；外部程式碼、host shell 與外部 storage 的 trust boundary 明確。
- 可替換性：核心測試可用 fake adapter 執行，不要求網路或真實 provider。

## 9. 第一版不做的事情

以下項目刻意排除，避免 roadmap 失控：

- OAuth / ChatGPT 訂閱登入與 Pi auth.json 共用。
- 完整複製 Pi TUI、theme system、terminal editor 與所有 keyboard shortcuts。
- 遠端 session server、團隊協作與雲端同步。
- 讓第三方 extension 在沒有 trust/permission policy 下自動執行。
- 將目前的 LocalShellBackend 包裝成「安全 sandbox」；若要 sandbox，另做明確 backend。
- 將所有 Pi package/npm 行為直接搬進 Python core；先完成 extension contract，再評估 package distribution。

## 10. 待決策事項

這些決策應在 M0 完成前鎖定：

| Decision | 建議預設 | 原因 |
|---|---|---|
| SQLite 位置 | user-level data directory，支援設定覆寫 | 避免把大量 session 寫進 repo；可跨 project 管理 |
| project session | 以 workspace identity 關聯，不預設寫入 repo | 降低意外提交 prompt/工具輸出的風險 |
| 外部 store | SessionStore protocol + optimistic version | 可支援 Postgres、HTTP、object storage 而不綁定 schema |
| command namespace | 內建 command 與 extension command 分 namespace | 避免第三方覆寫核心命令 |
| extension discovery | user-level、project-level file + Python entry point | 兼顧本地開發與可安裝 package |
| project trust | ask / always / never | 比單純 prompt 指示更可追蹤 |
| compaction provider | 使用目前 model 的可配置 summarizer，並保留 deterministic fallback | 避免摘要服務故障造成 session 不可用 |
| model descriptor | capability metadata 必填，provider-specific fields 可擴充 | 讓 vision/tool/context 檢查不靠字串猜測 |

## 11. 參考實作證據

本 roadmap 的 Pi parity 依據：

- Pi 將預設工具固定為 read、write、edit、bash，並透過 skills、prompt templates、extensions、packages 擴充：[Pi README](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/README.md:63)
- Pi 的 model/provider、subscription 與 model switching：[Pi README](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/README.md:97)
- Pi 的 session、branch 與 compaction：[Pi README](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/README.md:236)
- Pi 的 context files、skills 與 extensions：[Pi README](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/README.md:320)
- Pi 的 CLI modes、session options、tool/resource options：[Pi README](C:/Users/y4lin/gemini_repo/pi_coding_agent/pi/packages/coding-agent/README.md:514)
- 目前 repo 的 agent factory、CLI、tools 與 static prompt：[agent.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/agent.py:12)、[cli.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/cli.py:34)、[tools.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/tools.py:17)、[prompt.py](C:/Users/y4lin/gemini_repo/pi_deepagent/pi_deepagent/prompt.py:8)

## 12. PM 結論

目前最重要的不是先增加更多 tools，而是先完成 M0 的 event/session/tool/provider contracts。這四個 contract 一旦穩定，Slash commands、Context files、Skills、Extensions 與 Subagents 才能共用同一套 runtime，不會再次分裂成互不相容的實作。

建議產品交付節奏：

1. M0～M3：建立可串流、可切換模型、可控工具的 coding runtime。
2. M4～M6：建立可恢復的長期工作流與 command surface。
3. M7～M9：建立 Pi-style resource 與 extension ecosystem。
4. M10：把 DeepAgents subagents 提升成可保存、可治理的產品能力。
