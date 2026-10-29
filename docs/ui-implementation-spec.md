# Pi Workspace 實作規格

狀態：規格審核、TDD、獨立程式審查與最終驗收完成。2026-10-26。此文件將 ui-proposal 第一階段展開為可實作合約；ui-prototype.html 保留為示意，不作正式登入入口。審查與驗收紀錄見 ui-implementation-review.md；啟動方式見 web-workspace.md。

## 範圍與部署

交付可操作的本機 Web 工作台，沿用已確認的藍白色、微軟正黑體、圖示導覽與同一圓角長方形內的輸入框／送出控制。正式頁面不含示範任務、示範密碼或模擬計時器。

沿用 Python、既有 uvicorn（a2a extra 已有），以獨立 ASGI adapter、SQLite、原生 HTML/CSS/JS 實作，不新增套件、不修改既有 CLI/A2A 行為。啟動方式 `python -m pi_deepagent.web`，只綁定 127.0.0.1，一個 process、一個固定 workspace、一個固定 model。啟動參數／既有 .env 是管理員設定入口；不在瀏覽器提交 API key 或任意工作路徑。啟動驗證 workspace 存在且為目錄、model 有 provider、密碼已初始化。

本階段不提供 diff、任意檔案下載、A2A gateway、待答協定、模型設定表單、逐次命令授權、會員功能。結果是 Agent 實際輸出的文字，不能宣稱工具成功代表測試通過。既有獨立 A2A 不由此服務掛載或代理；日後整合須先加上同等驗證。

## S1 身分與權限

`init-admin` 透過 getpass 二次輸入，至少 12 字元；拒絕公開原型密碼。隨機 salt + scrypt hash 寫入服務資料目錄，無預設帳密。只接受 username=admin，沒有註冊。session 隨機 256 bits、只在伺服器保存摘要，絕對期限 8 小時；重啟全部撤銷。cookie HttpOnly、SameSite=Strict、Path=/；loopback HTTP 不設 Secure。資料目錄與 workspace 不得相同或包含於 workspace，以免 Agent 工具讀到憑證與 session。管理員仍須以作業系統權限保護資料目錄；這不是惡意 Agent 沙箱。

所有 `/api/*` 除登入外先驗 session，再驗 admin 角色、操作 allowlist、資源 workspace／owner。未登入 401、無權限 403、授權範圍內不存在 404。外部提交 role、owner、workspace 或路徑欄位拒絕，不信任前端權限。只允許預期 Host，變更請求必須 Origin 等於服務 origin，且 application/json；登入亦須 Origin，其他變更另須 session 對應 `X-CSRF-Token`。不提供 CORS。GET 沒有副作用。

登入錯誤統一訊息；每來源 5 次失敗／60 秒後 429，成功清除此來源紀錄，限制表有容量上限。稽核記錄 action、identity、target、allow/deny、時間，不記 cookie／密碼／prompt／模型金鑰。日誌不輸出原始 provider error；頁面也不回傳原始錯誤（避免包含密鑰）。公開資源只允許固定 index/css/js，不提供 filesystem serving。

登出撤銷 cookie 對應 session，前端立即清除私有資料。session 過期或撤銷後輪詢 401 即停止並回登入；背景工作繼續，重新登入可檢視。任何遲到的 HTTP 回應不能讓登出後 UI 重現內容。

## S2 任務與執行

SQLite 保存 tasks（不透明 UUID、owner=admin、workspace、title、status、messages、events、output、run_id、revision、updated_at），DB 不在 Agent workspace。草稿只留頁面記憶體，不存瀏覽器持久儲存。啟動時將 running/stopping 改 interrupted，保留內容，絕不重跑有副作用工作。

狀態：fresh → running → completed/failed/stopped；running → stopping → stopped 或實際終態；重啟 running/stopping → interrupted。completed/stopped/interrupted 可繼續新訊息；failed 可明確重試（不重複使用者訊息）或送新訊息。全服務同時只執行一個 run；重複或不同任務並行請求 409。停止是合作式取消，不撤回已發生的檔案／命令副作用，不保證終止外部子程序；UI 顯示「停止中」直到 runner 真正結束。

每個變更 POST 除登入／登出需 `request_id` UUID。服務在 DB 原子保存 request_id、方法／資源／payload fingerprint 與結果；同一 id 同一請求回相同結果，不執行兩次；id 重用於不同請求 409。前端傳送失敗保留相同 id，先查狀態／同 id 重送，不以新 id 自動重跑。建立任務為空任務，訊息 1..32000 字元，JSON body 上限 128KiB；拒絕未知欄位與非字串／空白訊息。

每輪建立實際 Deep Agent graph，將此任務的歷史 user/assistant 訊息傳入，不混入別的任務；不依賴私有 runtime 欄位。沿用 AgentLoopRuntime.stream 正規化事件；僅輸出安全活動摘要（開始、工具名稱、完成、失敗、停止），不洩漏私有推理／工具參數／原始環境。文字 delta 合併進當前回覆，儲存單調 revision。持久化的 user/assistant 文字可供重啟後續談；工具內部隱藏狀態不跨輪恢復。每輪 10 分鐘上限、輸出 1Mi 字元與活動最多 1000 筆；超限終止並回可讀錯誤，不自動重試。

終態規則：runtime error 記 failed 候選，不等待 run_end，但等 iterator 確實退出才釋放 slot 並發布終態；timeout／超限／沒有終態的 EOF 為 failed；run_end completed/aborted 對應 completed/stopped。手動 stop 只標記 stopping 並取消 runner，只有實際取消完成才 stopped；若完成先到則 completed，終態單次 CAS 不接受遲到覆寫。每輪輸出為獨立 assistant 訊息且有 run_id／complete，只有 completed 回覆才傳入下一輪歷史；failed/stopped 的部分回覆仍可檢視但不當成完成答案。retry 使用最後一筆 user，不再 append user，也不傳入其失敗輪 partial assistant。

timeout／超限的 failed 原因優先於取消產生的 aborted；取消清理期間仍占用全服務 slot，不接受下一輪。

ASGI lifespan startup 開啟 DB 並原子恢復 interrupted、清除 session 後才 ready；禁止同資料目錄多 process（OS 排他鎖）；shutdown 停止接收執行、要求 runner 取消並 await 完成後才關 DB。runner shutdown／timeout 均不可啟動下一輪。接受工作先在交易內保存 request ledger、訊息與 running run，commit 後排入服務持有的背景 task 再回 202；HTTP request 結束或斷線不取消 run。所有 worker 出口在 finally 釋放 slot；ledger 查重優先於 running 狀態衝突。不可取消的外部工具不是可回復交易，需在啟動說明及停止後提示。

輸出邊界遮蔽伺服器啟動時已知非空 API key/token/password/secret 環境值及憑證記錄，也遮蔽本服務發行的 session/CSRF token 及本次驗證密碼（登入失敗密碼不記錄）；在寫 DB、snapshot、活動與下載之前處理。delta 先合併再遮蔽，保留可能匹配的尾端避免跨 chunk 洩漏。送入模型的原始 prompt 留在當輪記憶體；儲存的文字亦遮蔽已知值。這不是任意機密的 DLP 或 Agent 沙箱；不宣稱能辨識未知檔案中的所有秘密。

## S3 HTTP 合約

所有回應（含錯誤、文字成果下載）均 `Cache-Control:no-store`、nosniff。頁面 CSP 僅 self、禁止 inline script、frame-ancestors none、form-action self。CSRF token 只存在 JS 記憶體。

| 方法／路徑 | 權限 | 請求 | 成功 |
|---|---|---|---|
| GET /, /app.css, /app.js | 公開固定資產 | 無 | 200 |
| POST /api/login | Origin | username,password | 200 identity,csrf + cookie |
| GET /api/session | admin:session | 無 | 200 identity,csrf,workspace 顯示名稱 |
| POST /api/logout | admin:session + CSRF | {} | 200，撤銷 session |
| GET /api/tasks | admin:read + workspace | 無 | 200 summaries |
| POST /api/tasks | admin:create + CSRF | request_id | 201 task |
| GET /api/tasks/{id} | admin:read + owner/workspace | 無 | 200 完整 snapshot |
| POST /api/tasks/{id}/messages | admin:run + CSRF + owner/workspace | request_id,text | 202 task |
| POST /api/tasks/{id}/retry | 同上 | request_id,expected_run_id | 202 task，僅 failed/interrupted；run 不符 409 |
| POST /api/tasks/{id}/stop | admin:stop + CSRF + owner/workspace | request_id,expected_run_id | 202 task，僅 running/stopping；run 不符 409 |
| GET /api/tasks/{id}/result | admin:read + owner/workspace | 無 | 保留相容文字匯出，UI 不再當作成果 |
| GET /api/tasks/{id}/artifacts/{artifact_id} | admin:read + owner/workspace + artifact membership | 無 | 200 application/octet-stream attachment；不存在 404 |

每 1 秒輪詢 list 與目前 snapshot；單一在途輪詢避免倒序覆蓋，背景／離線時降低頻率。中斷顯示「連線中斷，正在確認狀態」，保留草稿、停用執行控制直到重新取得 snapshot。不將中斷當作 run failed。沒有公開 SSE/WebSocket 或其他 API。

## S4 UI 與可及性

登入前無任務資料；左下登入，admin 欄唯讀、密碼 autocomplete=current-password，標籤與行內錯誤、pending 防重送；成功清密碼、聚焦頁面標題。登出清空 tasks/drafts/output、聚焦登入入口；手機先收選單並聚焦 menu。

上方只保留對話標示；工具摘要保存在各輪 assistant 訊息，以原生 details／summary 收折，頁內更新保留展開與焦點。一般文字只在聊天顯示；成果區只列檔案 metadata 與下載。新增任務進列表、切換保留草稿且不取消背景工作。送出後清草稿僅在 202 確認後，失敗保留。輸入與送出／停止同大圓角容器，Enter 送出，Shift+Enter 換行，Ctrl/Cmd+Enter 仍可送出；IME composing／keyCode 229 不送出，長按 Enter 不重複送出。內容區在視窗高度內獨立捲動，輸入區保留於下方；位於底部時跟隨更新，向上閱讀時保留捲動位置。失敗時顯示實際可操作提示及重試；需要使用者確認「可能再次執行已做過的操作」後才重試。成果下載成功完成後 pi_write／pi_edit 產出的 UTF-8 檔案快照，不依文字推測 artifact，規格與限制見 web-workspace.md。文字與檔名全部使用 textContent，不執行模型 HTML。completed 不顯示綠色測試通過。

有檔案時桌面 3 欄，無檔案時收起成果欄；1024 以下由右上成果入口開啟同一面板，Escape 關閉回入口；375/768 單欄無整頁橫捲。圖示有 aria-label，狀態文字＋圖示、focus-visible、觸控44px、正文16px／1.6、reduced motion。輪詢不搶焦點、輸入 selection 或捲動；只在狀態真正變更時 aria-live，mobile menu Escape 回入口。密碼及 token 不入 localStorage/sessionStorage/URL。

## S5 TDD 與發布 gate

先寫失敗的黑箱 ASGI/HTTP 測試，再實作。SQLite 真實暫存檔與真實 AuthService；只替換外部模型為可控的 async runner。覆蓋每個受保護 route 的未登入／無權限／有效 admin／失效 session、CSRF/Origin/Host、登入限制、輸入邊界、未知路徑、idempotency、單一執行競態、停止與完成競態、重試、重啟、歷史隔離與持久化、結果下載。Runner adapter 另用既有 deterministic graph 驗證正常化與歷史，不依賴外網或付費模型。

UI 用純邏輯測試覆蓋傳送未知結果、request_id 保留、logout 遲到回應、draft 保留；實際瀏覽器驗證登入、任務、送出、停止、重試、重載、mobile／keyboard、無 console error。送出聚焦停止；完成只在焦點仍在該控制時回輸入；手機開選單聚焦目前任務，選取後聚焦標題；輪詢保留列表焦點。長 URL／32000 字元不橫向撐頁，閱讀舊訊息不強制跳底。所有既有 unittest 與新增測試須 green，獨立 subagent 審 spec 無未解 issue 後才能實作；程式 review 與 unit-test-trust-review 無未解 issue 才可交付。保留審查處置與驗收證據，包括未用真實付費模型之限制。
