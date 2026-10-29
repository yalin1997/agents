# 本地 LLM：RTX 4090 24GB

使用 Qwen3.8-27B 的 Unsloth `UD-Q4_K_M` GGUF（16,464,440,224 bytes），
搭配 llama.cpp b11883 Windows CUDA 12.4 可攜版。
模型及執行檔放在 `D:\LocalLLM`，不加入 Git，也不需要新增 Python 套件。

## 啟動

在專案根目錄使用 PowerShell：

```powershell
.\scripts\local-agent.ps1
.\scripts\local-agent.ps1 --once "讀取 README.md，說明這個專案的用途"
```

腳本會先啟動模型服務，然後啟動既有 agent CLI。
模型服務已啟動時會重用它；agent 結束後服務仍會留在背景。
只想啟動模型 API：

```powershell
.\scripts\start-local-model.ps1
Invoke-RestMethod http://127.0.0.1:8080/health
```

預設只監聽本機 `127.0.0.1:8080`，模型名稱為 `qwen3.8-27b`。
16K context、單一 slot、GPU offload、Flash Attention、Q8 KV cache，
每次生成最多 4096 tokens，其中 thinking budget 為 2048。
這些參數可在 `scripts/start-local-model.ps1` 調整；變更後需重啟服務。
16K 包含提示、工具 schema、對話、工具結果及生成內容。Agent 現在會對 loopback OpenAI 模型使用本機上下文預算，避免大量工具結果原樣送回模型。

### 工具後的 context size 錯誤

日誌中的 `request (32541 tokens) exceeds the available context size (16384 tokens)` 表示工具回傳後的整個模型請求超過服務容量，不代表最後一個工具本身失敗。先前框架對未知模型採 170,000-token 摘要門檻，無法保護此 16K 本機服務。

本機 factory 沿用 Deep Agents 原生 `FilesystemMiddleware`、`SummarizationMiddleware` 及 `SummarizationToolMiddleware`，沒有自訂裁切／重試／摘要演算法。`PI_DEEPAGENT_CONTEXT_WINDOW=16384` 保留最多 2048 tokens 生成空間與 1024 tokens 餘裕，將可用輸入容量 13312 寫入 model profile。原生摘要採 profile 的 85% 觸發、10% 保留量；摘要輸入上限為 min(4000, 可用輸入容量/3)，避免摘要請求本身超量。原生計數器採每 token 約 2 字元的較保守設定，包含 system prompt 與工具 schema，但仍不是本機 tokenizer 的精確計數。

大型工具結果交由原生 FilesystemMiddleware 保存至 backend 的 `/large_tool_results/`，摘要前的完整歷史保存至 `/conversation_history/`；目前 FilesystemBackend 會將其放在工作目錄內，可能包含敏感內容，應以作業系統 ACL 保護。工具結果 offload 門檻為 max(256, 可用輸入容量/12)，大型使用者訊息門檻為 max(256, 可用輸入容量/4)，沿用框架的大小估算與例外工具處理。模型可透過原生檔案工具回查，不會把這些內部檔案自動當成果發布。Web 已保存的聊天文字保持原樣。

`compact_conversation` 與自動摘要共用同一個原生引擎及摘要事件。預設 general-purpose subagent 繼承原生 filesystem／自動摘要設定；自訂 compiled subagent 或自行覆寫 middleware 的子 Agent 必須自行配置。內部摘要串流不作為聊天回覆顯示。

大型工具回傳另外有兩項防守：`pi_bash` 不再在落檔前截掉 50,000 字元以後的輸出；filesystem 使用原生子類別補上保存失敗即中止的策略，不將超大原文退回模型，也不自動重跑工具。工作台以 `tool_output_persistence_failed` 顯示明確提示；門檻、實際保存、預覽與分段回讀提示仍由框架負責。詳細設計、驗收與限制見 [工具輸出審查](tool-output-review.md)。

唯一的本機相容處理是將 llama.cpp 的 `exceed_context_size_error` 轉成標準 `ContextOverflowError`，讓框架的恢復流程接手。相容層不重試、不裁切、不重新執行 graph／工具；一般 HTTP 400／驗證錯誤保持原樣。原生壓縮仍可能因必要提示過大、儲存失敗、模型容量設定不符或摘要請求失敗而無法完成，屆時查閱 diagnostics.log。雲端 provider、非 loopback 端點及使用者已建立的模型物件保留原設定。

變更程式後須重新啟動工作台。若調整模型服務的 context size，亦須將 `PI_DEEPAGENT_CONTEXT_WINDOW` 設為相同容量；單獨提高此環境變數不會擴大模型服務的實際容量。沒有更改本機模型服務、GPU 或啟動腳本設定。

## 其他專案入口

使用一般 CLI 或 A2A 入口時，也可在該 PowerShell 工作階段設定：

```powershell
$env:OPENAI_API_BASE = 'http://127.0.0.1:8080/v1'
$env:OPENAI_BASE_URL = 'http://127.0.0.1:8080/v1'
$env:OPENAI_API_KEY = 'local-no-key-required'
$env:PI_DEEPAGENT_MODEL = 'openai:qwen3.8-27b'
.\.venv\Scripts\python.exe -m pi_deepagent.cli --once "你好"
```

`local-agent.ps1` 只暫時設定子程序使用的環境變數，結束時還原，
不修改 `.env` 或原本的雲端 API key。原有 CLI 的 `--model` 仍可覆寫模型。

## 診斷與停止

日誌位於 `D:\LocalLLM\server.stderr.log` 和 `server.stdout.log`。
可用 `nvidia-smi` 查看顯存；模型載入後確認日誌顯示 layers 已 offload 至 GPU。
若顯存不足，先關閉其他 GPU 工作，再重啟服務。

停止背景模型服務並釋放其顯存：

```powershell
.\scripts\stop-local-model.ps1
```

腳本會核對執行檔路徑、模型檔路徑與 port，只停止符合的 llama-server。
未啟動時會顯示提示；可加 `-WhatIf` 預覽。若啟動時使用自訂
`-InstallRoot` 或 `-Port`，停止時也傳入相同參數。
停止前請先結束 agent 對話，避免中斷進行中的請求。
下次執行 `.\scripts\start-local-model.ps1` 或 `.\scripts\local-agent.ps1` 即可重啟。

## 來源

- 模型：https://huggingface.co/Qwen/Qwen3.8-27B
- GGUF：https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
- 推論程式：https://github.com/ggml-org/llama.cpp/releases/tag/b11883

## 此機驗證（2026-10-26）

- 模型 SHA-256：`322e194ff79741c7baa497c240f677f54b201b0efab44ca8e50f122b39123482`，與發布檔案相符。
- `/health`、`/v1/models` 和實際 chat completion 均成功。
- 真實 `create_pi_deepagent` 在獨立暫存工作區使用 `pi_read`、`pi_write`、
  `pi_edit`，讀取隨機內容、產生正確轉換結果、將加法函式的減法 bug 修正；
  直接檢查產出與執行函式均通過，整個短任務約 28 秒。
- `local-agent.ps1 --once` 已成功讀取本專案 `pyproject.toml` 並回答名稱。
- 測試期間服務日誌生成速度約 47 tokens/s；這是單次短任務觀察值，
  包含 thinking tokens，不代表所有任務的端到端速度。
- 16K 配置載入後整張 GPU 使用約 18.1 GiB（包含其他程序）。
- 現有設定、CLI、agent factory 共 13 項 unit tests 通過。
