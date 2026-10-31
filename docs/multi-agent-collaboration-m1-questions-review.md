# M1 第三批：typed user question 與原工作回答

日期：2026-10-31。狀態：**一般使用者問題的持久化／授權／投遞已驗證；M1 與完整 AC 尚未完成，0/31**。

## 實作範圍

`pi_deepagent/collaboration/questions.py` 的 `ask` 由 runtime 閉包提供 member grant 與 WorkAttempt，模型只提供 prompt／options；question ID、種類、到期、收件成員、工作、run attempt、政策與目標版本均由服務決定。只有 active work 可詢問，同一 WorkAttempt 不重複建立 pending 問題；尚有未處理回答也不能再詢問。詢問消耗一份共用 messages 額度，admin 回答不扣 Agent 額度。等待時間仍由 TeamRun wall budget 計量。

`QuestionService` 沿用真正 AuthService；`access.py` 集中 admin／session／operation 檢查，團隊草稿服務也呼叫此邊界。讀取及回答均在交易內重新驗證，父 run／問題歸屬檢查先於 replay。未登入、非 admin、無操作或 workspace 權限、登出及過期 session 都拒絕；登出不撤銷原本已核准的背景 member grant。

admin 以 question ID、question revision 與 typed `answered`／`declined` 回覆。答案是資料，不能核准能力、完成工作、通過 required gate 或建立新 attempt。保存回答、問題終態、工作 ready、原成員投遞、member sequence、event 與 request ledger 共用交易；失敗全部回滾。原 WorkAttempt 保持不變。回答包含權限宣告也不會變成 policy；其他 approval／manual-validation／非程式驗收使用各自的合約，沒有冒充一般聊天。

`mailbox.py` 將 answer 與 peer message 用同一 member sequence 排序、受同一 batch limit 及單一活動 turn 限制。回答只能在原 WorkAttempt 的 turn 領取；別的成員、無 work context 或別的 attempt 不能收到。回合中才到達的答案保留給下一批；只有可信任 runtime 記錄已知成功才 processed，unknown 保持 delivered 並阻擋重播。這是持久化記錄，不代表已執行 graph。

到期回答 409，並持久保存 expired；目標工作／policy 不一致則 superseded，不改寫重新指派的工作。stop、budget exhaustion、interrupted 及 continue 取消未決問題，舊答案不能喚醒新 attempt。明確 declined 保存答案及事件，供後續 Orchestrator 在原範圍重規劃；沒有自動重建問題。真正的重規劃與無可行路徑退出仍待 M3。

`migrations/004_user_questions.sql` 為加表 migration，包含問題、不可變答案、投遞、父子 FK、每工作唯一活動 attempt、pending 唯一鍵及終態／身份不可變 trigger。所有執行均在測試暫存 DB；沒有正式 migration。

## 實際驗證

`tests/collaboration/test_questions.py` **21 項**，使用真 SQLite、AuthService、兩連線 Barrier 及固定時鐘，不 mock 認證／資料庫／graph，也沒有啟動模型或容器。涵蓋原 WorkAttempt／原成員、精確 replay、不同答案搶答、answer／stop 競態、明確 stop-first、expired／stale policy／work revision、跨 run／workspace、同回合晚到答案、共享 member sequence、unknown、decline、登出背景工作及秘密遮蔽。注入 SQL trigger 故障驗證答案、工作、cursor、ledger 與 outbox 全部回滾。

最終協作 suite **121 通過、41.534 秒**；Windows 全 Python **309 通過、152.600 秒**。0 fail／skip。run／mailbox／question 以 seed 1031 打散順序 **58 通過、0 fail／skip**；測試曾逐檔、合併與完整 suite 執行，有限次重複未觀察到不穩定。前一批 Node 24 項仍是未修改 UI 的既有證據，本批沒有聲稱新 UI 驗收。

分支覆蓋採真正 `coverage --branch --source=pi_deepagent.collaboration`，沒有 exclude／omit／pragma：

| 模組 | 行覆蓋 | 分支覆蓋 |
|---|---|---|
| access | 13/13，100% | 2/2，100% |
| questions | 156/165，94.5% | 38/46，82.6% |
| mailbox | 156/158，98.7% | 62/64，96.9% |
| draft service | 107/107，100% | 24/24，100% |

依 unit-test-trust-review，這個持久化批次為**中等信任**：有真實競態、精確值／錯誤 code、原子性及變異證據；仍有 defensive service-actor 拒絕、找不到問題、遮蔽後選項碰撞／膨脹、封閉 run 的舊 pending 防守等未覆蓋分支。內部 SQL 沒有覆蓋 Docker、正式 runtime 或 HTTP 的安全性。CI 未加入 coverage gate；沒有安裝新依賴。

`m1_runtime_mutation_probe.py` 新增 question 到期、目標版本、parent scope、訊息計量、回答不得完成工作、pending answer 與 answer processed 的抽樣變異；既有 10 個 run／budget／mailbox 變異一併保留。先跑完整 baseline，再對各模組選擇相關 suite。必須有已完成 unittest 的結果標記，setup／import failure、來源不唯一、空 suite 或 skip 不能算 killed。個別原始日誌、實際失敗／error 數與用到的 test modules 保存於結果 JSON。

實際結果：**17/17 killed、0 survivor**；原有基礎合約／授權／交易變異亦重新驗證 **9/9 killed**。已讀取個別失敗紀錄核對為 assertion／實際 SQL 防線觸發，沒有把未執行到 unittest 的失敗計成 killed。這是 26 個抽樣變異，不能推論所有可能缺陷均會被抓出。

## 未驗證項目與後續依賴

- 尚未掛載 HTTP／UI；不能用直接 service 測試代替逐路由 session／CSRF／Origin／parent-resource 矩陣。
- 真實 graph interrupt／喚醒、等待時釋放模型／容器、到期計時器及 Orchestrator 消費 declined／expired 事件未接入。WorkItem ready 仍須經排程器正式派工，不能直接當成正在執行。
- 非程式成果驗收、五種 ApprovalRequest、manual-validation 與 validation-plan／criteria 版本尚待實作；一般 question API 明確不接受這些身份／種類。
- unknown 對帳、整隊完成屏障、取消外部工作及 legacy worker 反向活動鎖仍待整合。
- Docker 搬移仍只有可審查方案，映像未拉取；GPU 依管理員指示 pending。沒有新容器、真模型、乾淨 required gate 或候選發布證據。

## 重現及證據

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_questions -q
.venv/Scripts/python.exe -m unittest discover -s tests/collaboration -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
.venv/Scripts/python.exe scripts/collaboration/m1_runtime_mutation_probe.py --output <new-empty-evidence-path>
.venv/Scripts/python.exe scripts/collaboration/m1_mutation_probe.py --output <another-new-empty-evidence-path>
```

完整 framework／Web 測試需允許本機 loopback socket，不需要 GPU 或 Docker。證據根目錄維持 `C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`：`m1-questions-tests.log`、`m1-questions-full-python.log`、`m1-questions-coverage.json`、`m1-questions-shuffled.*`、`m1-questions-mutations/`、`m1-questions-foundation-mutations/`。索引 `collaboration-m1-questions-index.json` 記錄實際結果與 source／migration／evidence SHA-256；沒有正式 Agent run／candidate／validation plan，識別欄位為不適用，不冒填。
