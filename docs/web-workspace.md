# Pi Workspace 使用與驗收

這是正式本機 Web 入口，連接既有 Deep Agents runtime。`docs/ui-prototype.html` 仍是獨立示意頁，不提供正式身分驗證。

## 啟動

使用專案現有 Python 環境；Web 啟動器沿用既有 `a2a` extra 的 uvicorn，沒有新增套件。若環境未安裝該 extra，先由管理員按專案安裝流程啟用，啟動器會給出缺少套件提示。

在專案根目錄執行：

```powershell
.venv/Scripts/python.exe -m pi_deepagent.web init-admin
.venv/Scripts/python.exe -m pi_deepagent.web --workspace C:/path/to/project --port 8765
```

第一次初始化透過終端隱藏輸入密碼兩次，至少 12 字元。帳號固定 `admin`，沒有註冊或會員功能。服務沒有預設密碼，不會接受原型的示範帳密；已存在的憑證不會被初始化指令覆寫。

預設資料目錄是使用者家目錄的 `.pi-deepagent-web`，保存 scrypt 憑證、SQLite 任務及不含 prompt 的稽核日誌。可用 `--data-dir C:/private/pi-workspace` 指定；資料目錄不可在 Agent 工作目錄內，且應由作業系統 ACL 保護。同資料目錄只允許一個服務程序。

模型沿用 `.env` 的 `PI_DEEPAGENT_MODEL` 或 `--model provider:model`；工作目錄沿用 `PI_DEEPAGENT_WORKSPACE` 或 `--workspace`。provider API key 仍由本機環境設定，不能透過瀏覽器提交。沒有金鑰／模型服務不可用時，任務會保留並顯示可操作的錯誤，而不產生虛構成果。

服務只綁定 `127.0.0.1`。開啟 [本機工作台](http://127.0.0.1:8765/)，按左下角登入。請使用指定的 IP／port，服務會驗證 Host 與 Origin。

## 工作流程

- 左欄 `＋` 建立任務，輸入需求後按箭頭或 Enter 發送。Shift+Enter 換行；中文輸入選字時的 Enter 不會發送。Ctrl/Cmd+Enter 也可發送。
- 長對話在內容區獨立捲動，輸入框保留於下方；位於底部時跟隨新回覆，向上閱讀時保留目前位置。
- 可切換任務、保留各自頁內草稿；任務紀錄與成果索引保存在 SQLite，成果快照保存在服務資料目錄，重整與重啟可讀取。草稿不跨重新整理保存。
- 全工作台同時執行一輪。執行中箭頭變為停止；停止確認前顯示「停止中」。
- 上方只保留「對話」標示；每輪工具紀錄在聊天內收折，展開狀態於頁內保留。文字回覆只出現在聊天，不再複製到成果區。
- 成果區只列出實際檔案的名稱、大小與下載；沒有檔案時不佔側欄。窄螢幕使用右上成果入口開啟同一個面板，Escape 關閉並回到入口。
- 失敗或重啟中斷可明確選擇重試；會先提醒可能重複副作用。重試不會自動發生。
- 連線中斷保留需求並確認原請求；相同 request_id 的重送由後端去重，不能建立第二輪。
- 登出清除畫面私有資料並撤銷 session。背景工作繼續；重新登入可查看。服務重啟不會重跑未完成工作，會標示「已中斷」。

## 邊界

此版本是單一管理員、工作目錄與模型的本機工作台；沒有多會員、任意工作路徑、檔案 diff、A2A gateway、逐次命令授權或模型金鑰表單。既有獨立 CLI／A2A 沒有由此服務代理，不能視為已被 Web session 保護。

Agent 具備實際讀寫檔案與執行工具的能力；本服務不是惡意 Agent 沙箱。停止是合作式取消，不會回復檔案修改，也不能保證終止外部子程序。已知服務憑證／環境金鑰會在輸出邊界遮蔽，這不是未知機密的完整 DLP。

跨輪保留 user／已完成 assistant 文字歷史；中斷回覆可供檢視，但不傳入下一輪當作完成答案。工具內部狀態不跨輪恢復。每輪 10 分鐘、1Mi 字元、1000 活動上限。完成只表示 Agent 執行終止，不代表測試通過。

成果目前收錄成功完成一輪後，`pi_write`／`pi_edit` 明確回報已寫入的 UTF-8 文字檔；不依模型回覆內的路徑、Markdown 或程式碼區塊推測成果。只收錄工作目錄內的普通檔案，排除隱藏路徑、連結、二進位及超過 1 MiB 的檔案；每任務最多 20 個檔名，同名只列最新成果。經 shell 或其他工具產生的 PDF／圖片等尚未自動收錄，也不會把一般文字回覆假裝成檔案。停止／失敗輪次不發布新成果。

下載為完成時保存的快照，不是任意路徑檔案瀏覽器；仍套用已知秘密遮蔽，並以附件格式下載、不執行 HTML。`GET /api/tasks/{id}/artifacts/{artifact_id}` 驗證 admin、任務 owner／workspace 與成果歸屬。既有 `/result` 文字匯出 API 暫保留相容，但 UI 不使用它作為成果。舊任務未曾保存的檔案不會回溯推測，既有最後一輪工具摘要仍可在聊天展開。

## 本機模型在工具呼叫後失敗

### 查詢新錯誤

重新啟動服務並以 Ctrl+F5 更新後，失敗訊息下方提供「除錯資訊」收折區，顯示錯誤編號、UTC 時間、執行編號、發生階段、最後工具與分類提示。最後工具只供關聯，不表示該工具是肇因；「工具已回傳」也不等於工具成功。

原始例外訊息與可取得的堆疊只寫入服務資料目錄的 `diagnostics.log`，不直接回傳瀏覽器。預設位置為 `$env:USERPROFILE\.pi-deepagent-web\diagnostics.log`；啟動時會印出實際路徑，指定 `--data-dir` 時隨之改變。每筆為一行 JSON，包含 `id/task_id/run_id/phase/tool/code/message/traceback`，可依畫面上的錯誤編號查詢：

```powershell
Get-Content "$env:USERPROFILE\.pi-deepagent-web\diagnostics.log" -Tail 30 -Wait
# 搜尋包含輪替備份的紀錄（將下一行的文字換成畫面上的錯誤編號）
Select-String -Path "$env:USERPROFILE\.pi-deepagent-web\diagnostics.log*" -SimpleMatch "錯誤編號"
```

日誌每 2 MiB 輪替，最多保留 3 份備份；重試會產生新的 run/error 編號，舊筆可由日誌查詢。既有任務未曾保存的例外無法回補。日誌寫入失敗時，畫面會明確提示檢查磁碟空間與權限，不會讓任務卡住。

不主動記錄 prompt、工具參數／完整輸出、HTTP headers 或區域變數；例外文字仍可能包含供應商回顯的請求內容。已知憑證及常見 Bearer／URL 憑證會遮蔽，但不是未知機密的完整 DLP。日誌僅供有本機資料目錄權限的管理員使用，不作為成果或公開檔案下載。畫面上的分類提示經既有 admin／任務 owner／workspace 權限檢查；不新增未驗證入口。

堆疊涵蓋 graph、factory、串流與清理的例外；runtime 僅提供錯誤事件而無堆疊、逾時與上限事件，可能只有錯誤訊息及階段。`audit.log` 持續只負責身分與操作稽核。

### 已處理的模型相容性問題

另一項已確認的工具後失敗為本機 context 超量：請求 32,541 tokens、服務僅允許 16,384 tokens。現沿用 Deep Agents 內建摘要、compact 與大型結果 offload，將正確容量寫入 loopback 模型 profile；大型工具結果與摘要前歷史由原生引擎保存於 backend，支援回查。預設容量 16384，可用 `PI_DEEPAGENT_CONTEXT_WINDOW` 對齊服務設定。llama.cpp 的容量錯誤只轉成標準 ContextOverflowError，由內建摘要恢復，不自行裁切或重跑工具。詳見 [本機模型上下文控制](local-llm.md)。重試前仍須注意先前工具可能已生效。

已修正一項 Qwen／llama.cpp 相容性問題：Deep Agents 0.7.10 對 `openai:` 字串預設使用 Responses API；本機服務可接受第一輪，但工具執行後的 reasoning item 回送可能回應 `400: item['content'] is empty`。這個錯誤發生在工具執行之後，不表示工具本身沒有生效。

共用 agent factory 現在會對 loopback OpenAI 相容端點明確使用 Chat Completions。判斷依實際設定優先序 `OPENAI_API_BASE` → `OPENAI_BASE_URL`，支援 localhost、IPv4／IPv6 loopback；雲端／非 OpenAI provider 與預先建立的模型物件保持原設定。沒有移除工具或改動登入／權限驗證。修改後需重新啟動工作台 Python 程序；先前失敗任務的工具可能已執行，請確認產出再決定是否重試。

依據：[Deep Agents 模型設定與 Responses API 預設](https://docs.langchain.com/oss/python/deepagents/models#openai-and-the-responses-api)。本機已實際重現修正前 `pi_read` 完成後的 HTTP 400；修正後真實模型可完成工具回合。新增 SDK HTTP 格式與實際 DeepAgents 工具讀寫測試，並以 143 項完整 unittest 回歸驗證。

## 驗證指令

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
```

Windows 執行環境需允許 asyncio 的本機 socket；受限沙箱可能停在事件迴圈初始化，正常本機執行可驗證。

規格、逐項審查處置與最終驗收證據見 [實作規格](ui-implementation-spec.md) 與 [審查紀錄](ui-implementation-review.md)。測試中的可控模型不使用付費 provider；沒有將通過的離線測試宣稱為真實 provider 連線驗證。


## 對話封存與永久刪除

側欄「對話」顯示未封存 session，每筆右側可移入封存區；執行中與停止中的對話需先停止。封存區可唯讀查看與下載既有成果，還原後才能繼續送出或重試。

封存區支援單筆勾選、全選目前列表、取消全選，以及確認後永久刪除。永久刪除會移除 DB 對話、工具事件、相關請求快取與服務管理的成果下載快照；不刪除工作目錄實際檔案，也不清除診斷與安全稽核日誌。這是應用資料刪除，並非備份、磁碟或既有日誌的安全抹除。服務僅保留不含對話內容的刪除作業識別與舊請求 ID，避免重送舊請求重建資料。

若 DB 刪除已完成而快照清理失敗，畫面會明確提示；保留原 request ID 重試清理，包含服務重啟後。刪除確認不會自動執行於升級或啟動時。啟動時只做相容的 SQLite 欄位／表格遷移，舊 session 預設未封存。

API：`GET /api/tasks`、`GET /api/tasks?archived=true`；`POST /api/tasks/{id}/archive` 與 `/restore` 接受 `{request_id}`；`POST /api/tasks/purge` 接受 `{request_id, task_ids}`，回傳 `{deleted_ids}`。所有入口沿用 admin session、操作與 workspace 資源權限；POST 另驗證 CSRF 與來源。整批刪除會先驗證全部目標，任一無權限、未封存或正在執行即拒絕整批。

## 工具呼叫卡片

每次工具呼叫只有一張卡片，依 bash、read、write／edit、搜尋類顯示 SVG 圖示，未知工具用預設圖示。卡片顯示名稱、輸入摘要、文字狀態；預設收折，可用鍵盤展開輸入參數與 Return value。回傳更新原卡片，不另增完成編號。工具回傳不代表整個任務成功。

工具資料經已知機密遮罩後保存，單一文字欄位最多 32,768 字元，超量時標示截斷；以純文字呈現，不執行輸出內 HTML。舊紀錄未保存的輸入／回傳無法回補，會明確提示。優先以 run 與 call ID 配對；缺少 ID 時僅配對同輪同名且唯一待回傳的呼叫，歧義資料不猜配。


### 本次 UI 變更驗證（2026-10-26）

完整 Python unittest 188 項與前端 Node 測試 24 項通過；最後收尾另重跑 Web 測試 81 項，以及稽核調整後的 HTTP 測試 18 項，均通過。瀏覽器以獨立暫存 workspace／DB 與可控模型驗證桌面、390px 手機版、鍵盤展開、封存、唯讀、全選、取消刪除及還原；未操作正式資料，也未以此宣稱真實供應商連線已驗證。

新增測試使用實際 SQLite 與檔案系統驗證資料移除、快照清理、跨重啟重試及權限；只對清理失敗路徑注入 OSError。針對唯讀、列表篩選、批次驗證、舊請求重播、快取清除及工具遮罩做 6 項僅在記憶體內的變異檢查，6 項均被測試抓出；相關 58 項 Web service／HTTP 測試連跑三次通過。測得 service 行覆蓋率 92.5%、分支覆蓋率 85.5%。新功能測試可信度中高，但這是針對風險的抽樣變異驗證，並非全面變異測試；平台鎖定、部分系統 I/O 異常與其他既有模組仍有未覆蓋分支。
