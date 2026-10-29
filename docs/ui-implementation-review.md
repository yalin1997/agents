# 正式 Web UI 實作審查與驗收紀錄

## 依使用者要求改回原生 compact（目前實作）

已刪除自行撰寫的 LocalContextBudget 與其專用測試；下面「16K 本機上下文溢出」段落僅記錄先前已撤換的修復方案。現在共用 factory 使用 Deep Agents 原生 FilesystemMiddleware、SummarizationMiddleware 與 SummarizationToolMiddleware，自動摘要與 compact_conversation 共用同一個引擎。自訂程式僅配置容量／原生門檻，以及將 llama.cpp exceed_context_size_error 正規化成 ContextOverflowError；不自行裁切、刪除輪次或控制恢復重送。

PI_DEEPAGENT_CONTEXT_WINDOW 保留相容，預設 16384；扣除 2048 生成及 1024 餘裕後，model profile max_input_tokens=13312。沿用原生 85% 摘要觸發、10% 保留量，摘要輸入另限制為 min(4000,budget/3)。較低的原生 filesystem offload 門檻防止大型單一工具結果在壓縮前溢出，完整結果可由 backend 回查。內部歷史／工具結果檔案可能包含敏感資料，需以 OS ACL 保護，沒有新增對外檔案讀取入口或自動當作成果發布。

SDK/graph 回歸確認：大型 pi_read 結果完整保存至 large_tool_results；模擬明確容量拒絕時，原生引擎實際呼叫模型產生摘要、將舊歷史保存至 conversation_history，再繼續回答，pi_read 僅執行一次。新增最終輸出斷言抓到內部摘要串流混進聊天的問題，已按 lc_source=summarization 排除內部模型 token，保留正常模型串流及根層執行失敗。

真實本機模型驗收在隔離暫存工作區完成大型 pi_read：工具清單僅 pi_read、completed=True、error_count=0、原生 offload 檔案存在。未重跑使用者原始任務。完整 Python 167 項、前端 18 項測試通過；相容模組 local_model.py branch coverage 為 43/43 行、6/6 分支。記憶體突變驗證錯誤轉換移除、驗證錯誤誤分類、錯誤容量、摘要輸入不限長、compact 註冊遺失，5 項皆被測試抓到。高信心確認此次設定與相容層行為，但不宣稱能保證任意必要提示或所有模型都能放入 16K；摘要／儲存／服務配置本身仍可能失敗。

## 使用者重現後的根因修復：16K 本機上下文溢出

使用者提供的錯誤編號 `03c968cc-5880-4f14-9803-49a82b226fac`：glob 回傳後，模型請求為 32541 tokens、llama.cpp 容量為 16384 tokens，回應 HTTP 400 `exceed_context_size_error`。這是模型上下文拒絕，不能將最後工具判定為失敗肇因。已確認框架對沒有模型 profile 的模型採用 170000-token 摘要觸發門檻，與目前本機 16K 服務不符。

共用 factory 對 loopback OpenAI 字串模型加入 `LocalContextBudget`，以相同 middleware name 替換不適合小容量的預設摘要策略；目前版本的預設 general-purpose subagent 亦繼承。保留生成／估算餘裕，估算包含系統提示與工具 schema，必要時有標示地裁切工具回傳、完整舊輪次與早期工具批次，保留最新需求、系統指令及最新工具配對；不改寫 Web 保存的聊天文字。這是裁切，不宣稱語意摘要或完整歷史保留。

只有服務明確回報容量與 token 數時才有限次縮小模型請求，不重跑 graph／工具，不重送相同大小請求；無法保留必要內容時明確失敗。容量可由 PI_DEEPAGENT_CONTEXT_WINDOW 對齊實際模型服務，不變更服務、GPU 設定或雲端／預先設定的模型物件。診斷提示另將 context 容量不足與一般 HTTP 400 區分。

完整 Python 回歸 167 項通過；18 項前端測試通過。新增 6 項預算單元測試與 2 項 SDK/graph 大型工具回傳測試：50k 字元回傳可完成；明確容量 HTTP 400 可縮小模型請求且工具只執行一次；工具配對、頭尾內容、系統指令、當輪需求及原始訊息不被更改。初次完整回歸抓到新模組與既有 ContextLoader 的名稱衝突，已改用 context_budget.py 並恢復既有 context.py，最終全套通過。

真實本機模型驗證：沿用目前 .env 指定的 loopback 模型，在隔離暫存工作區呼叫一次 pi_read 讀取大型 probe.txt，觀察工具清單只有 pi_read、run_end completed、error 數為 0。未重跑使用者原始失敗任務，亦未變更其工作檔案。SDK 測試則使用隔離 HTTP 回應驗證明確容量拒絕的恢復行為。

依測試可信度 skill 進行 5 項記憶體突變：移除工具裁切、修改原訊息清單、破壞 tool_call_id、移除容量恢復、恢復 170k 門檻，均被測試抓到。context_budget.py 覆蓋 95/100 行、33/40 分支；尚未窮舉所有多模態內容、異常服務 body 與多次同步縮小路徑。綜合為中等至高信心的針對性修復，不保證所有任務均能放入 16K；過大的必要指令、工具定義或當輪需求仍須縮小或提高服務容量。

## 使用回饋修正：工具後失敗的除錯方法

Web service 原先將 runtime 的 error message 丟棄，只有通用錯誤；audit.log 也僅記存取結果。新增 diagnostics.log 與每次失敗的 UUID，關聯 task/run、時間、階段、最後工具、錯誤碼、原始例外及可取得的堆疊。錯誤文字最多 8000 字元、堆疊 16000 字元，先遮蔽已知憑證再截斷；2 MiB 輪替及 3 份備份。無法寫入時畫面提示，不阻塞任務終止。

聊天在失敗提示下增加預設收折的「除錯資訊」，保持鍵盤焦點與展開狀態，錯誤使用 role=alert；詳細內容可選取與捲動。依 UI/UX skill 的 error recovery 搜尋結果提供可追查編號與排查提示。分類提示透過既有受保護 task API，不增加日誌公開入口；原始 provider 訊息只進本機日誌，避免回顯請求內容洩漏到瀏覽器。最後工具不作肇因判定。舊失敗未保存的資訊無法回補。

驗證：69 項 Web Python 測試、18 項前端測試通過。涵蓋 runtime error／factory error、圖執行堆疊、逾時／上限、EOF、重試重啟、日誌輪替、寫入失敗、已知憑證及 Bearer／URL／JSON key 遮蔽、admin／viewer／跨 owner／登出存取。既有兩項保密回歸曾抓到原始錯誤進入 task payload，已改為本機詳細訊息加瀏覽器固定分類提示，未放寬測試。

以記憶體突變移除瀏覽器訊息隔離、移除已知秘密遮蔽、讓寫入失敗誤報成功，3 項均被測試抓到；未修改正式檔案。branch coverage：diagnostics.py 62/62 行、18/20 分支；runner.py 64/64 行、9/10 分支；service.py 359/390 行、124/146 分支。尚未逐一注入所有清理／啟動 I/O 錯誤；本次沒有呼叫真實模型來重現使用者這次的錯誤，不能從工具名稱列表判定真正原因。

隔離瀏覽器實際驗證登入、Enter 發送、可控失敗、除錯資訊收折及捲動、重整保留錯誤編號；圖片 `pi-chat-diagnostics.png` 保存在本次 visualization 目錄。啟動時印出實際日誌路徑，完整查詢指令見 web-workspace.md。日誌仍可能包含未知機密，須以本機 ACL 保護，不宣稱完整 DLP 或作業系統沙箱。

日期：2026-10-26。對象：`ui-implementation-spec.md` 及後續 `pi_deepagent/web` 正式工作台。原型 `ui-prototype.html` 不作為正式登入或後端安全的驗收證據。

## 規格審查

已閱讀 `AGENTS.md`、`security-requirements.md`、`ui-implementation-spec.md`，並依 `ui-ux-pro-max` 審查 UX、可及性與驗收可測性。主 Agent 已通知：規格的全部獨立審查問題已修正並放行。此處記錄本審查員可直接核對的 UX 結論；其他專員的安全與後端細項應由其實際審查紀錄補充，不以本文件替代。

本審查員結論：**無 UX／可及性規格阻擋問題，可進入實作。** 這是規格放行，尚非程式或發布放行。

| 審查點 | 處置與規格證據 | 結論 |
| --- | --- | --- |
| 傳送結果未知可能重複執行 | S2 request ledger／相同 request_id；S3 連線中斷先確認狀態 | 已規定，不自動建立新請求 |
| 任務切換、背景工作與草稿 | S2 草稿只留頁面記憶體；S4 切換保留草稿、不取消背景工作，202 才清草稿 | 已規定 |
| 停止與重試的副作用 | S2 停止中直到實際 runner 結束，不保證取消外部子程序；S4 重試需確認副作用 | 已規定 |
| 登出與 session 失效的遲到回應 | S1 清私有內容、401 回登入、遲到 HTTP 不重現內容 | 已規定 |
| 輪詢與鍵盤焦點 | 原 UX 審查提出具體送出／完成／手機選單焦點測例，S5 已納入，S4 要求輪詢不搶焦點／selection | 已採納，待實作驗證 |
| 長內容與捲動 | 原 UX 審查提出長 URL／32000 字元及閱讀舊訊息測例，S5 已納入，S4 禁整頁橫捲 | 已採納，待瀏覽器驗證 |
| 登入、輸入與圖示語意 | S4 有標籤、行內錯誤、autocomplete、pending、IME、aria-label、文字狀態、觸控目標 | 已規定 |
| 真實結果不冒充測試通過 | 範圍與 S4 均區別完成、工具成功與測試通過 | 已規定 |

## 最初驗收計畫

這是實作前的測試計畫，當時尚未執行；最終結果與證據見末節，不能將清單本身視為通過證據。

| ID | 情境 | 通過條件 | 證據方式 |
| --- | --- | --- | --- |
| UI-01 | 未登入、正確／錯誤登入、429、重載 | 未登入無任務；錯誤就地說明；pending 不重送；成功清密碼且聚焦標題 | 瀏覽器＋邏輯測試 |
| UI-02 | 建立與切換任務 | 無示範任務；新任務出現在列表；任務草稿、內容、進度互不污染 | 瀏覽器＋邏輯測試 |
| UI-03 | 送出確認與傳送結果未知 | 僅 202 後清草稿；未知結果保留 request_id，停用執行控制，先確認狀態 | 受控 HTTP 回應測試 |
| UI-04 | Enter、Ctrl/Cmd+Enter、IME | Enter 換行；快捷鍵送出；composing 期間不送出；不重送 | 邏輯測試＋鍵盤 |
| UI-05 | 送出、停止、完成競態 | running 轉 stopping 直到實際終態；控制正確啟用／停用；焦點不丟失，不誤稱副作用已回復 | 可控 runner＋瀏覽器 |
| UI-06 | failed／interrupted 重試 | 顯示可操作原因；先確認副作用；帶 expected_run_id；不重複 user 訊息 | 邏輯測試＋HTTP 測試 |
| UI-07 | 登出、session 過期、遲到回應 | 清 tasks／drafts／output；停止受保護輪詢；遲到回應不還原資料；焦點回正確入口 | 受控回應測試＋瀏覽器 |
| UI-08 | 對話／活動／成果及下載 | 輸出以文字呈現；成果模式不重複；copy／download 成功與失敗均有回饋 | 瀏覽器＋HTTP 測試 |
| UI-09 | 持續輪詢與背景完成 | 不重建目前焦點節點；不重置 textarea selection；不搶捲動；僅真正狀態變更播報 | 瀏覽器＋DOM 測試 |
| UI-10 | 手機選單及鍵盤 | 展開聚焦目前任務；選取收起、聚焦標題；Escape 返回入口；無隱藏焦點目標 | 瀏覽器鍵盤 |
| UI-11 | 375／768／1024／1440px 與粗指標 | 規格指定欄位可用；圖示有名稱；粗指標44px；沒有整頁水平捲動 | 瀏覽器量測／截圖 |
| UI-12 | 長 URL、路徑、32000 字元、長輸出 | 自然換行／區域捲動；不撐頁；使用者閱讀舊訊息時不強制跳底 | 瀏覽器 |
| UI-13 | 無障礙與視覺基本項 | 微軟正黑體優先、正文16px／1.6、focus-visible、reduced-motion、明確發話者、對比與狀態語意 | CSS／DOM 檢查＋瀏覽器 |
| UI-14 | 執行與資料的真實性 | 無原型密碼、模擬計時器、虛構完成資料；沒有付費模型驗證時明確記錄限制 | 程式審查＋驗收紀錄 |

## 測試可信度審查範圍

待程式與測試就緒後，獨立核對：

- 測試是否驅動真實 UI 狀態轉換及可觀察結果，而非只斷言字串存在或 mock 自己的回傳值。
- 未知結果、logout 遲到回應、草稿、單一在途輪詢與順序競態是否含反例與非成功分支。
- DOM／瀏覽器檢查與純邏輯測試是否互補；不能以 mock DOM 通過宣稱真實焦點、版面或讀屏已通過。
- Python 新增／修改測試須另依 `unit-test-trust-review` 審查。安全 route 矩陣、實際 SQLite、AuthService 與 runner adapter 結果由對應專員提供。
- 審查尚未執行的項目與無法驗證的限制必須保留；不能以「全部 green」推論未覆蓋流程安全。

## 程式與發布狀態

正式 UI 原始碼已進行首次獨立靜態審查：`static/index.html`、`app.css`、`app.js`、`state.mjs` 與 `tests/ui-state.test.mjs`。瀏覽器驗收及 Python 測試可信度由主 Agent／對應專員另行完成。發布 gate 尚未放行。

### 首次程式審查問題

以下行號為首次審查版本，後續修正可能移動。沒有修改實作檔。

| ID | 嚴重度 | 問題與重現依據 | 建議與狀態 |
| --- | --- | --- | --- |
| UI-R1 | P1 | `app.js` 的 `api()` 在 `await response.json()` 前檢查 epoch，但讀取完成後不檢查。讀 body 期間登出／重新登入，舊 401 能清除新 session；登入／boot 的舊成功回應亦可更新身分。以實際 api 函式、可延遲 body 的 fetch 重現：epoch 改變後 `clearPrivate()` 仍被呼叫。 | 每個 await 邊界後、所有影響狀態或錯誤處理之前重驗 epoch。未結案。 |
| UI-R2 | P2 | `refresh()` 的完成分支在 `render()` 前聚焦 prompt；上一輪 render 仍把該欄位設 disabled，focus 不生效。`clearPrivate()` 處理 401 也未安排可見登入入口焦點；登出時登入入口 pending disabled，立即 focus 同樣可能失敗。 | 先 render 啟用控制後，條件式恢復焦點；session 失效與確認登出後給予有效入口。未結案。 |
| UI-R3 | P2 | `render()` 每次都重建具有 `role=status` 的 badge 子節點，即使狀態未變；輸入每一字與每輪 polling 都觸發。這繞過 announce 的去重，不符合只在狀態真正變更才 aria-live。 | 狀態未變時保留 badge DOM／文字。未結案。 |
| UI-R4 | P2 | `updateContent()` 在每次 revision 變更重建 content 與 aside-result，會移除目前聚焦的成果 copy／download 按鈕；串流輸出期間操作成果即可遭遇。列表按鈕已採穩定節點，但成果控制尚未同等處理。 | 維持動作按鈕穩定節點，更新文字；或精確恢復同區域同動作焦點。未結案。 |
| UI-R5 | P2 | 四個 state 測試全部通過，但僅 import `state.mjs`，不執行 `app.js` 的 fetch、JSON body、401、polling、recoveries、busy、logout 或 DOM。測試名稱含 late logout／unknown result，實際只測同步 Map／epoch helper，無法防止 UI-R1／R2。 | 增加驅動實際 controller 的可控非同步測試；至少覆蓋 body 延遲後 epoch 變更、相同 request_id 的恢復、202 清草稿時機、互換任務與完成焦點。未結案。 |
| UI-R6 | P3 | Spec S4 要求 375／768 單欄，但 CSS 使用 max-width:767px，768px 仍保留 sidebar + main 兩欄。 | 明確與 spec 對齊 768px 行為。未結案。 |

### 已取得的驗證證據

- `node --test tests/ui-state.test.mjs`：4/4 通過。只證明 state helper 的同步合約，不代表實際 controller 或瀏覽器通過。
- UI-R1 使用從實際 `app.js` 擷取的 `api()`，讓 response header 先回、JSON body 延遲；變更 epoch 後才 resolve 401 body，觀察到 `clearPrivate()` 呼叫。此為局部確定性重現，不冒稱全瀏覽器 E2E。
- 靜態確認已具備 textContent 呈現模型文字、發話者標籤、IME composing 保護、每任務草稿、request_id helper、粗指標44px、長字串換行、穩定任務按鈕節點及無前端持久儲存密鑰。

### 第二次複核與測試反證

首次問題 UI-R1、R2、R3、R4、R6 的對應實作已核對：body 成功讀取後重驗 epoch；完成先 render 再恢復焦點；session 失效／登出焦點落在有效入口；badge 僅狀態改變才寫入；成果動作按名稱與區域恢復焦點；單欄 breakpoint 已改為 768px。以上仍需主 Agent 的真實瀏覽器驗收補足 CSS 與 native focus 行為，不能以 VM DOM 完全代替。

UI-R5 已新增 `tests/ui-app.test.mjs`，實際載入 `app.js` 到 Node VM，僅以受控 fetch／DOM 替代瀏覽器。與 state 測試合計 9 項通過。新增測試覆蓋 body 延遲、完成焦點、相同 request_id 的恢復與草稿／revision、不變狀態播報與成果動作焦點，足以防止對應已知回歸。

執行五項 **in-memory production mutation spot checks**，不改 production 檔：

| 變異 | 初次結果 | 複核 |
| --- | --- | --- |
| 移除 JSON body 後 epoch 檢查 | killed | 遲到 body 測試失敗 |
| 移除完成後 prompt focus | killed | 完成焦點測試失敗 |
| 不變狀態仍重寫 badge | killed | htmlWrites 斷言失敗 |
| 移除成果動作焦點恢復 | killed | 成果焦點測試失敗 |
| settle 每次重新產生 request_id | 首次 survived | 原 fixture UUID 固定而掩蓋變異；主 Agent 改為每次遞增後，baseline green，此變異 killed |

修正 fixture 後五個變異均可被殺死。這是針對已知風險的 spot check，不是全套 mutation score，也不代表未涵蓋分支均可信。

### 新增問題 UI-R7：遲到的網路例外污染新 session

**P2，未結案。** `api()` 對成功的 fetch／JSON body 已重驗 epoch，但舊請求若在 epoch 變更後 reject，就會直接進入呼叫端 catch。`refresh()` catch 只檢查 `state.identity`，新的登入恰好為真，因此舊連線例外會令新 session `connected=false` 並 render，鎖住新的工作控制。

確定性重現：使用實際 app.js 與既有 VM fixture，啟動舊登入 refresh → 等待 fetch 已進入 → clearPrivate 並建立新登入及有效任務 → reject 舊 fetch。新增斷言「新登入 send.disabled 仍為 false」失敗，觀察為 true。此測例僅在記憶體執行，未寫入測試或 production 檔。

建議在 api 的 catch 中重驗 epoch，過期時一律轉為 StaleResponse；或在每個非同步呼叫端 catch 先核對 epoch。須同時測 fetch rejection 與 response.json rejection；目前 body 成功後的守衛不能涵蓋這兩種例外路徑。修正與回歸測試完成前，本審查員不放行最終 UI gate。

### UI-R7 結案與程式審查放行

主 Agent 已為 api 加入涵蓋 fetch／body 例外的 epoch catch 守衛，並加入「late rejected fetch cannot disconnect a new session」controller 回歸測試。另將 polling guard 由布林值改成 epoch，避免舊登入未結束的 poll 擋住新登入，或舊 finally 清除新 poll 的鎖。

本審查員複核結果：

- 實際執行 `node --test tests/ui-state.test.mjs tests/ui-app.test.mjs`：**10/10 通過**。
- 額外兩個 in-memory controller 測例通過：舊 JSON body rejection 轉為 StaleResponse；舊 pending poll 不阻擋新登入 refresh，舊 rejection 不清除新身分。未改 production／測試檔。
- UI-R1 至 UI-R7 均已結案，無新增阻擋程式問題。**UI 靜態程式與已覆蓋的 Node 邏輯測試審查放行。**

此放行不替代真實瀏覽器驗收、安全後端審查、Python unit-test-trust-review 或付費模型驗證。VM fixture 不具備真實排版、原生 modal、CSS 隱藏及停用控制的完整焦點行為；這些仍由主 Agent 實測與記錄。

### UI-R8：停止中保留鍵盤焦點（已結案）

主 Agent 的真實瀏覽器驗收另發現：停止被接受後，原生 disabled 的送出／停止按鈕造成焦點掉回 body。此為首次 VM fixture 無法完整模擬的原生焦點行為。

修正為 stopping 狀態僅設 `aria-disabled=true`，保留可聚焦的同一控制；onclick 明確拒絕 stopping／busy／離線操作，CSS 提供停用外觀。終態輪詢先 render 再依目前焦點恢復 prompt。獨立靜態複核沒有新增問題；沒有把 aria-disabled 當成自動阻止事件，實際 handler 已有防護。

新增 stopping 回歸測試覆蓋：停止被接受後 button 非原生 disabled、aria-disabled=true、焦點仍在該控制，終態後回到 prompt。獨立執行 `node --test tests/ui-state.test.mjs tests/ui-app.test.mjs`：**11/11 通過**。UI-R8 結案；UI 程式與目前 Node 測試範圍維持放行。原生瀏覽器的修正後複驗證據由主 Agent 補充。

### UI-R9：列表摘要與完整任務同 revision（已結案）

主 Agent 的真實瀏覽器驗收發現：重載後切換到尚未載入完整內容的任務，摘要先觸發 render；完整 snapshot 同 revision 到達時，被相同 render signature 略過，對話維持空白入口。

獨立複核修正：`WorkspaceState.accept()` 僅在含 messages array 的完整任務回應上記錄 `detailRevision`；內容 signature 同時包含 task revision 與 detailRevision，區分摘要與完整內容的到達；未載入 detail 時顯示「載入中…」。較舊 revision 的拒絕規則仍保留，摘要 merge 不清除已載入 messages。任務切換也清除舊 feedback。

新增 opening-summary controller 測試以相同 revision 的 summary／detail 驅動實際 `app.js`，並從 DOM 文字斷言已保存回覆可見，而非僅斷言 state marker。獨立執行 state＋app 測試：**12/12 通過**。UI-R9 結案，無新增阻擋 issue；維持先前程式審查放行與瀏覽器驗證範圍限制。

### UI-R10：完成時恢復焦點不改變閱讀位置（已結案）

主 Agent 最終檢查發現：終態恢復輸入框的預設 `focus()` 可能捲至頁底，打斷仍停留在舊訊息的閱讀。修正僅將該恢復呼叫改為 `focus({preventScroll:true})`；原先只有焦點仍停留在停止控制時才恢復的條件保持不變。

獨立複核：完成焦點回歸測試除確認欄位已啟用及 activeElement 外，增加對傳入 preventScroll 選項的斷言；實際執行 state＋app 測試 **13/13 通過**（目前亦含 IME／Enter 快捷鍵測試）。程式符合不搶捲動的規格，無新增 issue，UI-R10 結案並維持 UI 程式審查放行。測試驗證的是瀏覽器 API 呼叫合約，真實捲動位置的觀察仍應列於主 Agent 的瀏覽器證據。

## 最終整合驗收與交付 gate

以下為主 Agent 根據檔案、實際命令輸出與本機瀏覽器觀察完成的最終核對，覆蓋前文的原始驗收計畫。歷次審查中的「未結案／待驗證」描述保留作歷程；最終狀態以本節及各項結案段落為準。

### 規格與後端審查處置

三位 subagent 分別審核 UX／可及性、執行狀態與 runtime、安全與權限；規格修訂後全部放行，才開始 TDD。採納的規格問題包括 lifespan／commit 後排程、非正常 EOF 與終態優先序、stop/retry 的 expected_run_id、失敗 partial assistant 的歷史排除、所有敏感回應 no-store，以及已知憑證的跨 chunk 遮蔽。沒有以未審核規格直接開發。

後端採交叉 code review：interaction_review 審 auth/app/CLI，visual_review 審 service/runner，兩者不以自己的程式自審作為唯一放行依據。主 Agent 亦核對範圍隔離與整合。

| Issue | 修正及回歸證據 | 最終狀態 |
| --- | --- | --- |
| 重啟恢復會改到其他 workspace／owner 的任務 | startup 僅處理目前 scope；test_startup_does_not_recover_foreign_workspace_or_owner_tasks | 已修正 |
| cached create 可繞過資源權限 | ledger 回傳前以 get_task 驗 scope；test_cached_create_cannot_bypass_owner_workspace_guard | 已修正 |
| init-admin 可能先把憑證寫進 workspace 才拒絕啟動 | 讀密碼／寫檔前驗資料目錄；test_cli_rejects_workspace_credentials_before_password_prompt | 已修正 |
| 超限時底層 graph 尚未清理便釋放 run slot | runner 明確逐層 await aclose；test_nested_real_runtime_iterator_cleanup_holds_slot_on_limit | 已修正，visual_review 獨立重跑 service 25 tests 放行 |
| 工具名稱先截斷，漏掉長秘密前綴 | 完整名稱先去敏再截斷；test_tool_name_redacted_before_truncation | 已修正並複核 |
| UI 非同步、焦點、播報與摘要／詳情更新問題 | UI-R1 至 UI-R10 的逐項回歸與實際瀏覽器複驗 | 已修正，accessibility_review 放行 |

**最終程式審查沒有尚未解決的 issue。** 這是目前規格與測試範圍內的審查結論，不是不存在未知缺陷的保證。

### 測試結果與可信度

最終重跑命令及結果：

| 命令 | 結果 |
| --- | --- |
| `.venv/Scripts/python.exe -u -m unittest discover -s tests -q` | 139 tests，40.781s，OK，無失敗／跳過 |
| `node --test tests/ui-app.test.mjs tests/ui-state.test.mjs` | 13 tests，全部通過，無失敗／跳過 |
| `.venv/Scripts/python.exe -m coverage run --branch --data-file .coverage-web -m unittest discover -s tests -p test_web_*.py -q` | 新增 Web 測試 51 tests，13.843s，OK |
| `node --check pi_deepagent/web/static/app.js`、`python -m compileall -q pi_deepagent/web` | 通過 |

完整 unittest 中 A2A 的故障／timeout 測例會輸出預期的錯誤日誌與 traceback，CLI 參數拒絕測例會印 usage；最終 exit code 為 0、結果 OK，沒有把這些預期錯誤分支排除或跳過。

Web coverage 的實際分子／分母：行 722/784 = **92.1%**；分支 225/270 = **83.3%**。範圍包含新增 web package，沒有以 omit／pragma 隱藏缺口。

| 模組 | 行覆蓋 | 分支覆蓋 |
| --- | --- | --- |
| auth.py | 95.5% | 82.1% |
| app.py | 90.7% | 83.7% |
| service.py | 91.9% | 82.8% |
| runner.py | 100% | 90% |
| __main__.py | 83.3% | 83.3% |

依 unit-test-trust-review，測試對本次已知高風險合約有高信任度：真實 SQLite／scrypt／session，外部模型才替換為可控 runner；另用真正 DeepAgents graph＋FakeMessagesListChatModel 測公開 adapter 與歷史相容性。auth 5、service 5、UI 5 項 in-memory mutation spot checks 均被測試殺死；UI 曾有固定 UUID 掩蓋重送變異，修正 fixture 後重新殺死。這是 15 項針對性反證，不是全面自動 mutation 分數。

尚未覆蓋的分支主要包含部分初始化／清理失敗、其他平台鎖分支與異常輸入的替代路徑；沒有據此宣稱 100% 覆蓋或所有平台皆已測。覆蓋原始資料已移到本次 visualization 產物目錄，專案根目錄的測試產物與已確認的失敗測試暫存已清理。

### 需求與證據對照

| 規格／驗收項目 | 最終證據與結果 |
| --- | --- |
| S1／UI-01、07：admin、權限、登出與失效 | AuthService 真雜湊與 expiry/revocation/rate-limit 測試；每個 private route 的未登入／錯角色／撤銷矩陣。瀏覽器正確／錯誤登入、重整恢復 session、登出後 taskButtons=0、output 空、password/draft 長度=0，焦點回登入 |
| S1：Host／Origin／CSRF、no-store、秘密與固定資產 | ASGI 黑箱拒絕測試、結果下載 headers、跨 chunk 秘密測試、固定 static allowlist／CSP 測試；沒有提供額外未驗證的 Agent gateway |
| S2／UI-02、03、05、06：任務、去重、停止、重試 | 真 SQLite 的單一執行 slot、ledger 跨重啟、stop/完成競態、EOF/timeout/超限、歷史隔離、interrupted、nested cleanup tests；瀏覽器看到進行中→停止中→已停止與副作用提示、確認後重試且未重複 user 訊息 |
| S3：HTTP 合約與持久化 | tests/test_web_app.py 覆蓋 session、list/create/get/messages/stop/retry/result/logout；本機瀏覽器經正常 cookie/CSRF 呼叫同一 ASGI app，模型僅由 fixture 注入。重載後任務與對話可再次讀取 |
| UI-02、04、09、10：草稿、鍵盤與焦點 | 實際切換 A/B 任務回到 A，草稿仍為 A；Ctrl+Enter 送出後焦點在停止，終態回 prompt；IME／plain Enter／Ctrl/Cmd+Enter controller test。手機展開聚焦目前任務，Escape 回 menu，選取收起並聚焦標題 |
| UI-05：原生 stopping 焦點 | 修正後實際 DOM：activeElement.id=send、native disabled=false、aria-disabled=true；終態後 activeElement.id=prompt |
| UI-08：活動、成果、複製及下載 | 瀏覽器活動顯示真正開始／終態摘要；成果頁 inspector 隱藏，不重複；複製後顯示已複製成果；下載內容／權限／no-store 由 HTTP 測試驗證。未宣稱已驗證實體瀏覽器的下載檔案落點 |
| UI-09：遲到回應、播報、成果焦點與 full snapshot | 實際 app.js 的 Node VM 延遲 fetch/body/rejection、request replay、草稿/revision、badge 不重寫、成果焦點測試；瀏覽器重載後切換不同任務已可顯示完整對話，沒有同 revision 空白問題 |
| UI-09、12：閱讀舊訊息時完成 | 加強完成焦點測試先 RED，再以 preventScroll:true 修正為 GREEN，accessibility_review 複核放行。原生瀏覽器以長對話驗證：進行中 focused=send、scrollY=0；完成後 focused=prompt、scrollY 仍=0，沒有跳至頁底 |
| UI-11、12：斷點及長內容 | 375/768/1024/1440px 的 document scrollWidth 未超過 viewport；375/768 nav 隱藏、1024 收 inspector、1440 三欄。實際送出 32000 字元長網址，375px 下 messageLength=32000，scrollWidth=360≤375，composerWidth=328 |
| UI-13：字型、觸控、顏色與文字 | 實際 computed font family 微軟正黑體優先、正文16px；375px send 寬44px。CSS 已具 reduced-motion、focus-visible、speaker labels。色票計算對比：正文/白14.56、muted/畫布5.51、白/primary5.37、danger/淡紅5.93，均≥4.5 |
| UI-14：真實性與邊界 | 正式 static 無範例任務、原型密碼或模擬計時；正式 runner 連接 create_pi_deepagent 與 AgentLoopRuntime。瀏覽器 QA fixture 的回覆明示可控模型測試，不冒充付費模型或實際檔案成果 |

瀏覽器驗收未觀察到 JavaScript console error/warn；正式版面截圖保存為本次產物 `pi-workspace-implemented.png`。沒有做專用螢幕閱讀器、實體觸控、完整 200% zoom／高對比系統模式認證；CSS 與鍵盤驗收不冒充完整 WCAG 認證。未使用真實付費 provider，使用者需自行初始化正式 admin 密碼並提供可用模型設定。工作台仍依規格為本機單 admin、單 workspace、單 model；後續階段的 diff、A2A gateway 與逐次工具授權沒有宣稱完成。

交付入口：`python -m pi_deepagent.web`；詳見 [使用與啟動方式](web-workspace.md)。保留既有 CLI 與未相關的未提交變更，未提交／推送／部署，也未新增依賴。

## 交付後修正：本機工具回合 HTTP 400

使用者回報真實 Qwen 工具呼叫後失敗。進一步驗證指出原有離線 graph 測試沒有涵蓋實際 provider 的工具結果回送協定：本機 `pi_read` 已成功，接著 Responses API 回傳 `400: item['content'] is empty`。Deep Agents 0.7.10 對 openai: 字串的預設與本機 llama.cpp 的 reasoning item 相容性是此次根因，並非前端送出或權限檢查故障。

修正共用 agent factory：只在 OpenAI provider 指向 loopback 時，預先以 `use_responses_api=False` 建立模型；依 OPENAI_API_BASE 優先於 OPENAI_BASE_URL 的順序判斷。雲端、其他 provider、預先配置模型物件不變，不放寬驗證與工具權限。沒有重試已發生副作用的舊任務。

驗證證據：

- 端點選擇回歸先觀察六個 localhost／IPv4／IPv6 subcase 失敗，再修正通過；包含環境變數優先序、非 loopback 與模型物件的反例。
- 真正 DeepAgents graph／runtime 執行 pi_write→pi_read，檢查實際檔案、工具事件及最終輸出；外部模型為確定性假模型。
- 真正 ChatOpenAI／OpenAI SDK 的 wire test，只替換底層 HTTP 傳輸：要求兩次 /v1/chat/completions，驗證第二次請求包含正確 tool_call_id 與讀檔內容；若走 /responses 則 fixture 回傳同樣的 400。
- 真實本機 Qwen 三次請求均走 /v1/chat/completions，工具回合 completed、沒有 runtime error；最後以明確相對路徑與內容參數執行 pi_read→pi_write，暫存 copy.txt 內容逐字比對成功。一次較寬鬆的複製指令未通過檔案逐字比對，沒有把該次模型行為算作檔案驗證成功；確認協定修正及檔案結果分別驗證。
- 全部 unittest **143 tests，40.180s，OK**；前端 **13/13** 通過。後續僅擴充端點邊界 subcase，再跑 agent **8/8** 通過。
- unit-test-trust-review：五個 in-memory 協定／端點選擇 mutants 均 killed，回復 baseline green；沒有修改 production 檔做變異。覆蓋量測包含 agent.py 行40/41、分支7/8與 runner.py 行56/56、分支9/10；後增的非 loopback subcase 另經窄測試通過，沒有把舊覆蓋數據冒稱成後增測例的新量測。

需重新啟動正式工作台程序才能載入修正。調查時只發現既有原型 http.server，未辨識到運行中的 pi_deepagent.web，沒有停止或替換使用者程序，也沒有替使用者設定正式密碼。
# 使用回饋修正：長對話與 Enter 發送

- 根因：`.app` 只有最小高度，聊天 `.content` 缺少高度約束與 overflow，長對話撐高整頁，使原有 scrollTop 保留邏輯無法正常作用。
- 修正：工作台限制在 100dvh（100vh fallback）；主欄允許縮小，聊天、任務列表及成果摘要各自捲動，輸入區保留於下方。手機任務列表改為視窗內覆層，避免開啟時把主欄推出畫面。
- 鍵盤：Enter 發送、Shift+Enter 換行，保留 Ctrl/Cmd+Enter；IME composing／229 不發送，長按不重複觸發，停用輸入時不觸發停止按鈕。
- 驗證：鍵盤回歸測試先紅後綠；前端 14 項全通過。隔離的真實瀏覽器流程以 Enter 發送 65 行訊息並完成第二輪對話；Shift+Enter 保留換行，向上閱讀後回覆更新仍維持 scrollTop=0。1280×720 及 375×667 的頁面尺寸均未溢出，聊天內容有獨立捲軸，輸入區底部仍在視窗內。
- 本次未修改後端，未重新執行 Python 全套；沿用既有服務，重新整理頁面載入新 CSS／JS 即可。

# 使用回饋修正：單一內容位置與工具收折

- 移除活動／成果分頁，只留下對話標示。工具摘要隨各輪 assistant 訊息保存，使用原生 details／summary 預設收合；輪詢保留展開與鍵盤焦點。舊資料最後一輪事件可相容顯示一次。
- 一般文字只在聊天顯示。成果區只渲染檔名、大小及下載，無檔案不佔側欄；窄螢幕入口開啟同一 DOM 面板，Escape 關閉並回到入口。
- 新增完成時文字檔快照：僅辨識 pi_write／pi_edit 明確成功回報，工作目錄內、非隱藏／連結、UTF-8、1 MiB／檔、20 個檔名／任務；同名更新只保留一張卡。停止／失敗不發布新檔案。既有 SQLite payload 加入可選 artifacts 欄位，舊資料預設空陣列，沒有 schema migration 或新依賴。
- 新下載路由共用既有 admin session／角色檢查，另驗 owner／workspace 與 artifact membership；覆蓋未登入、無權限、有效 admin、登出／過期、跨任務及檔案遺失。已知秘密遮蔽，所有檔案均 attachment + octet-stream + nosniff，HTML 不在頁內執行。舊 `/result` 僅保留相容文字匯出，UI 不再使用。
- TDD：新增成果案例在實作前因缺 artifacts 欄位失敗；完成後全套 152 項 Python／17 項前端測試通過。補測確認空工具回覆不當作空 assistant 傳入下一輪，以及檔名數量上限下仍可更新同名成果。
- unit-test-trust-review：新增測試使用真實 SQLite、暫存工作目錄、AuthService 及快照讀寫；只用可控 runner 隔離外部模型。額外 5 個記憶體變異（隱藏檔、遮蔽、成果發布、完成狀態、工具歷史）均被針對性測試捕捉，不改寫磁碟原始碼，不宣稱系統性 mutation score。
- 最終分支覆蓋：artifacts.py 行 47/47、分支 15/16；service.py 行 337/365、分支 118/140；app.py 行 220/242、分支 84/100。未覆蓋的 artifact 分支是解析後越界（前置相對路徑／連結檢查已拒絕常見輸入），未進行惡意程序競態或作業系統 ACL 安全測試。
- 瀏覽器：1280×720 桌面與 375×667 窄螢幕確認單一聊天、工具收折、檔案下載及面板 Escape；DOM 回覆計數為 1、成果卡為 1、舊分頁為 0。實際下載測試 report.md；隔離 fixture 內使用真正 pi_write 寫入檔案，沒有呼叫付費模型。截圖保存在本次 visualization 目錄的 pi-chat-tools-artifacts.png。
- 邊界：目前不收錄 shell／其他工具產出的二進位、PDF／圖片；舊任務未保存的檔案不回溯推測。這是明確限制，不以聊天文字冒充 artifact。服務不是惡意本機程序的 OS 沙箱。交付需重啟 Python 服務並重新整理。

