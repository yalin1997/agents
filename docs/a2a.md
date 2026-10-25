# Pi DeepAgent：一層 A2A 1.0 委派

這是既有 `pi_deepagent` 的可選整合：本地主 agent 透過真正 HTTP 連線，委派給一個使用同一 factory 建立的遠端 DeepAgent。沒有 2D map、MinIO 或多層委派；預設工作是一般研究／文字產出，缺必要資訊時反問。

## 使用的公版元件

- 協定：**A2A 1.0**，JSON-RPC 與 SSE；不是先前討論的 0.3.0 wire format。
- 官方 Python SDK：**`a2a-sdk==1.2.3`**，版本鎖在 `pyproject.toml`／`uv.lock`。
- Server：官方 `AgentExecutor`、`DefaultRequestHandler`（SDK V2）、`TaskUpdater`、Agent Card／JSON-RPC routes。
- 儲存：官方 `DatabaseTaskStore` + SQLite；不自行實作 A2A task schema。
- Client：官方 `A2ACardResolver`、`create_client`、`ClientConfig`；不自行拼 JSON-RPC HTTP request。
- Agent：沿用 `create_pi_deepagent()`，以 DeepAgents 的 `response_format` 表達內部結果。這個內部結果不是 A2A 新協定。

參考：

- [官方規格](https://a2a-protocol.org/v1.0.0/specification/)
- [SDK 1.2.3 與支援版本](https://github.com/a2aproject/a2a-python/tree/v1.2.3)
- [官方 runnable samples](https://github.com/a2aproject/a2a-python/tree/v1.2.3/samples)
- [官方 AgentExecutor](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/server/agent_execution/agent_executor.py)

SDK 的公版路由負責 methods、wire serialization、版本檢查、標準錯誤與 task 執行協調。應用只決定如何執行 DeepAgent、何時要求輸入，以及如何向使用者顯示結果。只啟用這次需要的 JSON-RPC binding；gRPC、REST、push notification、認證整合與 0.3 相容模式不啟用，也不宣稱支援。

## 安裝

在 PowerShell 進入專案：

```powershell
cd C:\Users\y4lin\gemini_repo\pi_deepagent
uv sync --extra a2a
```

設定模型 provider 的 API key，以及兩端要用的模型。沿用原專案的環境變數，不需額外 A2A API key。請在各終端自己的環境中設定，不將 key 寫進程式、session file 或提交至 Git。

```powershell
$env:PI_DEEPAGENT_MODEL = 'google_genai:gemini-3.6-flash'
```

模型名稱是原專案預設，可換成你帳號實際可用、支援工具呼叫與結構化輸出的模型。兩端可使用不同模型。

## 啟動真正的一層委派

終端一：啟動 server。

```powershell
uv run --extra a2a pi-a2a serve --workspace . --port 41241
```

終端二：啟動主 agent。

```powershell
uv run --extra a2a pi-a2a chat --workspace . --url http://127.0.0.1:41241
```

第一輪可輸入：

> 幫我寫一封活動邀請信。收件對象、活動內容與時間都還沒有提供，請先向我詢問必要資訊，不要自行編造。

主 agent 呼叫 `dispatch_remote_agent`。遠端 agent 需要資訊時，畫面顯示 `TASK_STATE_INPUT_REQUIRED` 與遠端原始問題。下一次輸入直接送回同一個 remote task，不再讓主模型猜測要回答哪個工作。遠端可能再問一輪，也可能產生正式 artifact。自然語言問題的實際內容由模型決定。

```text
CLI → 主 DeepAgent → dispatch_remote_agent → SDK client → HTTP
    → SDK handler → PiAgentExecutor → 遠端 DeepAgent

遠端 structured_response
    input_required → TaskUpdater.requires_input → 使用者回答同 task
    completed      → add_artifact + complete   → 顯示正式成果
    rejected       → reject                   → 顯示拒絕原因
    exception      → failed                   → 安全錯誤訊息
```

主端不是只把模型的最後文字當成功。程式以 SDK task snapshot 決定呈現反問或正式 artifact；即使主模型誤稱已完成，也不覆蓋遠端狀態。一次 session 同時只管理一個遠端 task，符合一層委派的範圍。

互動命令：

- `/status`：讀取目前 task。若上一個請求結果不明，從 task history 中確認 message ID 是否已被接受。
- `/cancel`：透過標準 CancelTask 取消目前工作。
- `/attach TASK_ID`：僅用於未知送出結果；驗證 task history 含原 message ID 後才接回。
- `/exit`：結束本地主程式；不偷偷取消遠端工作。

主 session 預設存 `.pi-a2a/client.json`。重開相同 chat 指令會載入它；pending 問題的答案回原 task。不同對話使用不同 `--session-file`，禁止兩個程序共用同一檔案。

## 不經主模型，直接研究 A2A 操作

這些指令使用相同官方 SDK client，便於把模型行為與 protocol 行為分開觀察。只有 server 需要模型 key。

讀取 Agent Card：

```powershell
uv run --extra a2a pi-a2a card
```

送新工作，預設 SSE：

```powershell
uv run --extra a2a pi-a2a send '請寫邀請信，缺少必要資訊時先問我。'
```

程式會印出 task ID／context ID。假設回傳的 task ID 是 `TASK_ID`，後續指令將它換成實際值：

```powershell
uv run --extra a2a pi-a2a send '對象是工程團隊，活動是週五下午三點的技術分享。' --task-id TASK_ID
uv run --extra a2a pi-a2a status TASK_ID
uv run --extra a2a pi-a2a cancel TASK_ID
uv run --extra a2a pi-a2a tasks
```

`status` 輸出 SDK 的完整 JSON snapshot，可觀察 history、status 與 artifacts。`tasks` 是標準 ListTasks，一頁預設最多由 SDK 決定的數量；如需全面檢索，使用 SDK 的 page token 分頁，不把第一頁當全部資料。

改用非串流、等待本輪任務到達待答或終止狀態：

```powershell
uv run --extra a2a pi-a2a send '請說明 HTTP 與 SSE 的差別。' --no-stream
```

立即取得 task，再自行查詢／取消：

```powershell
uv run --extra a2a pi-a2a send '比較三種訊息佇列，列出詳細取捨。' --return-immediately
```

本機 endpoint：

```text
GET  http://127.0.0.1:41241/.well-known/agent-card.json
POST http://127.0.0.1:41241/a2a/jsonrpc
```

版本 1.0 的方法如 `SendMessage`、`SendStreamingMessage`、`GetTask`、`CancelTask`、`ListTasks` 由 SDK 選擇與序列化。狀態是 `TASK_STATE_INPUT_REQUIRED`、`TASK_STATE_COMPLETED` 等標準 enum；Part 採 `text`／`data` 等內容欄位，沒有 0.3 範例中的 `kind`。不要把前文的 0.3 raw JSON 直接送到這個 endpoint。

## 持久化、錯誤與並行

- Server task/history/artifacts 存 `.pi-a2a/tasks.sqlite`，可用 `--database` 指定另一個位置。待答與已完成 task 重啟後仍可讀；待答 task 能用原 task ID 續接。
- Server 在執行途中重啟，啟動時把遺留的 submitted/working task 標為 failed，不假裝仍在運作、不自動重跑。不是背景運算恢復系統。
- 預設 server 單次模型執行上限 180 秒，client 整體觀察上限 200 秒；以 `--timeout` 調整。超過 server 上限回 failed；client 逾時不代表 server 失敗。
- 已知 task ID 但通訊結果不明，先 `status`；尚無 task ID，以 `tasks` 檢查 history 的 message ID。chat 會保存不確定狀態並阻止再次 dispatch；可使用 `/attach` 恢復。
- 不自訂 messageId 去重保證、不盲目 retry mutation、不宣稱 exactly-once。這符合採官方 SDK，而不另造一套公司去重協定的範圍。
- terminal task 不重啟。chat 的下一個新需求會建立新 task；直接 `send --task-id` 只允許待答／待授權 task。
- 同一 remote task 的執行協調、取消及儲存版本由 SDK 的 V2 handler 管理。多個 task 可並行；不同主 session 使用不同檔案。此部署只運行一個 server process，不開多個 uvicorn workers 共用 database。
- Server worker 失敗不將 provider 例外內容送回 client；詳細原因留在本機 server log，可依 task ID 查找。
- SSE 結束後會以 GetTask 讀完整 snapshot，不把斷線當完成、不重複拼接 artifact chunk。

## 執行邊界

這是完整的**本機單使用者、一層委派**實作。Server固定bind `127.0.0.1`；HTTP未加認證，不供公開或多租戶部署。沒有部署／CI變更，也沒有額外架設公司記憶服務。

`include_pi_tools=False` 不加入自訂的 `pi_read/pi_write/pi_edit/pi_bash`；DeepAgents 自帶的 workspace tools 仍依其 backend 運作。因此請給兩端合適的 workspace，不把含secret的目錄當研究workspace。未啟用 `--shell`，沒有為這次功能新增任意shell工具。

## 測試

```powershell
uv run --extra a2a python -m unittest discover -s tests -p test_a2a.py -v
uv run --extra a2a python -m unittest discover -s tests -v
```

A2A測試實際啟動uvicorn、連loopback socket，使用官方SDK client/server。包含Agent Card、SSE、非串流、多輪、正式artifacts、取消active/waiting task、隔離、重啟續接、標準錯誤、worker timeout、結構化輸出及主agent的真實DeepAgents graph工具呼叫。

測試用可重現模型／worker取代付費provider，以驗證真實graph與協定流程，執行不需要API key。正常 `serve/chat` 使用真正配置的模型，沒有自動fallback到測試模型。環境沒有provider key時，不能宣稱已驗證付費模型的連線／帳號可用性。
