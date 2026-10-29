# 超大工具輸出防守：設計與交付審查

日期：2026-10-26。範圍：使用者核准的兩項修正；沿用 Deep Agents 0.7.10 的原生落檔與 compaction。

## 行為契約

1. `pi_bash` 不再提前丟棄超過 50,000 字元的命令輸出。成功、非零退出及 timeout 已取得的 stdout／stderr 保留既有文字格式與退出／逾時標記。
2. 原生 `FilesystemMiddleware` 仍負責超大工具文字的門檻、完整保存、預覽、檔案位置與分段讀取指引。保存成功時，模型收到檔案路徑及 `read_file(offset, limit)` 提示。
3. `SafeFilesystemMiddleware` 僅覆寫同步／非同步的大型 ToolMessage 處理入口：原生保存失敗回傳原文，或處理超大回傳時拋出例外，都改為拋出 `ToolOutputPersistenceError`，不將原文繼續送進模型。
4. 不自動重新執行工具／graph。錯誤不代表命令未執行；已發生的副作用不回滾。
5. 工作台沿用既有錯誤與診斷 UI，分類為 `tool_output_persistence_failed`，明確顯示「工具已執行，但大型輸出保存失敗；本次執行已停止，未自動重跑工具」。

## 實作與相容性

| 檔案 | 修改 |
| --- | --- |
| `pi_deepagent/tools.py` | 移除 `_bash` 結尾的 50,000 字元裁切；不更改 `pi_read`、命令解析或 shell 行為 |
| `pi_deepagent/tool_output.py` | 原生 filesystem 的最小失敗策略相容層；保存錯誤只包含受限長度的工具名稱、call ID、錯誤分類及原因類別 |
| `pi_deepagent/agent.py` | factory 在其他模型路徑也採用安全 filesystem，保留原生預設門檻 |
| `pi_deepagent/local_model.py` | 本機 filesystem 換成安全子類別；原有本機門檻、摘要及 compact 配置不變 |
| `pi_deepagent/web/diagnostics.py`、`service.py` | 將 runtime 展平成一般錯誤的保存失敗重新分類，顯示固定安全提示 |

相容層名稱保持 `FilesystemMiddleware`，覆蓋框架原有位置，避免重複註冊 filesystem 工具，並保留預設 general-purpose subagent 的同名設定繼承。沒有修改 site-packages、增加依賴、資料庫 migration、HTTP 入口、登入或下載權限。

原生目前未提供保存失敗即中止的設定。相容層依賴 `_process_large_message`、`_aprocess_large_message`、文字擷取及大小估算的內部契約；框架升級時必須重跑契約測試。保存位置仍是 backend 的 `/large_tool_results/`，可能含敏感內容，以既有工作區／作業系統權限保護，不自動發布成使用者 artifact。

## TDD 與驗收證據

- 第一輪先新增測試：確認舊實作會截掉成功／非零退出輸出、保存失敗不停止、命令中段資料無法從保存結果取得，然後才修改產品程式。
- 第二輪先新增工作台診斷測試：確認舊實作缺少保存錯誤分類與明確提示，然後才補診斷流程。
- `tests/test_tool_output.py` 共 10 項測試，涵蓋完整中文 stdout／stderr、非零退出、timeout、單行長文字、多訊息 Command、真實 subprocess、同步／非同步保存失敗（None、error result、exception）、保留原生非保存錯誤，以及停止後不再呼叫模型／重跑工具。
- 整合測試使用真實 framework、檔案系統與 subprocess，模型部分為腳本式 fake model；證明落檔及後續原生分段讀取的工具鏈，不將此測試宣稱為模型自主決策驗證。
- 真實模型另外驗證：使用既有設定的 loopback 模型與獨立暫存工作區，`large_probe` 回傳約 52 萬字元，目標是第 20,001 行的隨機值，未提供給模型。模型實際只呼叫一次 `large_probe`，接著呼叫 `read_file`；完整檔案逐字一致、回讀包含目標、最終答案正確。暫存資料自動清除；此 probe 不執行命令，命令工具由前述真實 subprocess 整合測試驗證。
- 故障注入在獨立 Python 程序的記憶體內進行，不改動工作區檔案。五項變異全部被測試抓到：恢復命令裁切、取消保存失敗阻擋、取消大小判斷、將原文洩漏到錯誤、破壞原生 middleware 註冊名稱。

驗收紀錄：

- Python 完整測試：188 通過，0 失敗（最終 coverage run）。
- 前端測試：24 通過，0 失敗。
- 新增相容層：37/37 statements、8/8 branches 覆蓋。
- `git diff --check` 通過。
- 原始測試／coverage／probe／故障注入紀錄位於本聊天的 visualization 目錄，分別為 `tool-output-final-tests.log`、`tool-output-final-coverage`、`tool-output-live-probe.json`、`tool-output-mutations.json`。

## Code review 結論

針對上述變更進行人工檢查：原生註冊名稱及合併路徑、同步／非同步對稱性、完整輸出與工具身分保留、例外／診斷不含工具原文、保存失敗終止語意，以及變更未增加外部入口。已修正審查中發現的診斷缺口：失敗訊息需保留安全的原因類別（`backend_write_failed` 或例外類別），而非將 backend 原始錯誤或工具輸出寫入日誌。最終無待修正的已知問題；這是本次實作者的人工 review，未宣稱獨立審查者批准。

## 明確邊界

- 此修正保護原生通用文字結果落檔路徑；不宣稱能精確限制任何模型的總 token、平行工具批次總量或多媒體內容。
- 原生排除工具（例如 ls、glob、grep、read_file）及其既有分頁／裁切語意維持不變。單行長文字完整保存已驗證；不新增字元位移讀取 API，原生行分頁不代表能讀到任意超長單行的每一段。
- 自訂 compiled subagent 或自行覆寫 middleware 的 agent 仍需自行配置；沒有修改使用者傳入的 subagent 規格。
- `pi_bash` 仍使用 `capture_output` 收集解碼文字，不提供原始位元組保存、無限輸出的記憶體保證或跨 stdout／stderr 時序重建。直接呼叫 Python 工具不經 factory 時，呼叫端自行管理完整回傳大小。
- 保存失敗可能留下部分檔案；不宣稱內容已持久保存，也不自動刪除、重試或回滾。一般 error result 只能得知 backend 回報保存失敗；日誌原因類別不等於已確認磁碟／權限根因。
- 沒有新增自動刪檔政策；累積磁碟用量仍需管理。程式變更需重新啟動服務後才生效。
