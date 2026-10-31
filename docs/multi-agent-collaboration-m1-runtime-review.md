# M1 第二批：RunAttempt、累積預算及持久 mailbox

日期：2026-10-31（跨夜完成驗證）。狀態：**領域持久化部分已實作／驗證；M1 未完成，AC 仍為 0/31**。這批沒有啟動 Docker、解除其暫停、搬移資料、拉取映像、啟動 GPU 或正式套用 migration。前一批證據見 [M1 第一批](multi-agent-collaboration-m1-review.md)。

## 修改位置與實際行為

- `pi_deepagent/collaboration/runs.py`：服務內部使用的 run／attempt 記錄、不可變成員快照、持久頂層活動 fence、queued start 與 stop 的 revision 屏障。明確 continue 追加新 attempt，保留舊終態與累積用量；終態間隔不計費。interrupted 保留 fence，不自動重跑。這不是對外 start API，也沒有宣稱已停止任何外部程序。
- `budgets.py`：SQLite 交易內預留／消耗／釋放額度，總額以 used＋reserved 判斷。額度不足先持久化 stopping／failed intent，再回錯誤；unknown 不退款、不換 request ID 重跑。更換 grant 仍視為同一 actor；grant 精確綁定 run、attempt、member、policy，模型不能指定它。
- `mailbox.py`：同 run 隊友直接送訊息、固定廣播收件清單、每成員單調序號、reply／work／成果 hash 歸屬檢查、分頁及遮蔽。正文指紋使用原內容 hash，避免不同秘密都被遮成相同文字後錯誤去重。已知秘密不存入正文／events／ledger；遮蔽後超過 32,000 字元回明確 400，沒有額度消耗。
- 每次 claim 原子建立獨立 MemberTurn 與固定訊息批次，由唯一鍵限制同成員活動 turn。只有可信任 runtime 的已知成功才標 processed；期間收到的訊息保持 accepted，回合結果帶回尚待處理數。unknown 保留 delivered、禁止自動重送；run 重啟記錄會把活動 turn 標 interrupted。停止排空不能越過活動／unknown turn 或預留額度。
- `migrations/002_runs_and_budgets.sql`、`003_mailbox.sql`：具父子資源 foreign keys、唯一鍵、單調計數與不可變歷史的加表 SQL。WorkItem／WorkAttempt 與成果參照表目前只提供後續派工／整合所需的持久身分及關聯，尚無完整工作排程或成果產生器；登記參照不等於驗證通過。
- `storage.py`：明確、帶 checksum 的多版本 migration，可指定測試用 target version。開啟資料庫不自動升級；舊 DB 升級要求新備份。新版測試實際覆蓋 v1→目前 schema、舊 Web DB→目前 schema、保留 team／ledger／舊 tasks，以及備份還原。僅在暫存 fixture 套用。

## 已執行驗證及邊界

測試：`tests/collaboration/test_runs.py`、`test_mailbox.py`、`test_storage.py`，另以原有完整 suite 驗證共用儲存／框架與單 Agent／CLI／A2A 回歸。本批新增測試使用真正 SQLite 與多連線 Barrier，沒有 mock SQLite 或執行 graph／Docker／模型。完整 suite 的框架測試仍使用可控模型，不能當成真模型驗收。帳密及 DB 位於各測試 TemporaryDirectory；測試時鐘固定，UUID 不作順序假設，沒有 sleep 同步。

最終 collaboration suite **100 通過、25.293 秒**；完整 Windows Python **288 通過、78.908 秒**。兩者均 0 失敗／skip。既有 Node **24 通過、0 fail／skip**。完整 Python 日誌內的刻意錯誤分支／CLI argparse 訊息不是額外失敗，以 unittest 結果及程序 exit 0 核對。

覆蓋以 `coverage --branch --source=pi_deepagent.collaboration` 取得，無 omit／exclude／pragma 排除：

| 模組 | 行覆蓋 | 分支覆蓋 |
|---|---|---|
| budgets | 77/77，100% | 28/28，100% |
| mailbox | 145/147，98.6% | 60/62，96.8% |
| runs | 211/224，94.2% | 54/70，77.1% |
| storage | 175/179，97.8% | 48/52，92.3% |

依 unit-test-trust-review，本批持久化測試的信任程度為**中等**：重要競態／權限／去重／未知結果有值與錯誤 code 斷言及變異抽查，但 RunBinding 的 integration／validation constructor 路徑、部分重複終態拒絕與 migration 備份故障分支尚未完整覆蓋。不能把此覆蓋率提升為 service actor、實際停止、硬預算或端到端功能已驗收。沒有 CI coverage gate；本批未修改 CI。

新變異抽查 **10/10 killed、0 survivor**：exact actor grant、累積 wall time、預算邊界、unknown actor／不可退款、未排空不得完成、晚到訊息保留、attempt 投遞屏障、秘密遮蔽、原內容去重。最終 run／mailbox 37 項按 seed 1030 打散順序亦 **37/37 通過、0 skip**；逐檔、合併、完整 suite 與多次 mutation baseline 均可獨立執行，沒有觀察到失序或不穩定。這是抽樣變異與有限重複，沒有宣稱全面 mutation testing。

抽查在獨立子程序記憶體中改一處，未修改磁碟原始碼。既有 9 個基礎合約／授權／原子性變異也已重新執行，**9/9 killed**。其中部分 mutant 由 SQL 不變量先拒絕，這是實際資料庫防線的證據，不宣稱每次皆由 Python assertion 抓到。

測試開發時曾有一個 fixture 在 queued 已直接 stopped 後再次呼叫 finish_stopping；修正為先啟動 fixture 再測 draining，沒有放寬正式狀態檢查。正文遮蔽膨脹邊界另補 regression 並重跑最終 suite，前一版 287 項結果保留，不冒充最終版證據。

## 尚未完成的整合

1. 所有新增方法目前是內部 repository，沒有新增公開 HTTP／工具入口。admin session／操作／父子資源檢查必須在後續 adapter 接入後逐路由驗證；現有 grant 測試不能代替該矩陣。
2. 團隊 fence 尚未反向接入既有單 Agent worker；目前 prepare 只讀取 legacy tasks 作相容性拒絕，不可藉此宣稱全系統互斥已完成。
3. turn claim／finish 僅記錄持久狀態，沒有執行 graph。完成訊息批次也不等於 WorkItem 或 TeamRun 完成；真正結算仍須在同交易檢查訊息、工作、failure、決策、required gates 及外部 leases／journal。
4. 外部 Docker／Git intent-result journal、資源 lease、背景程序、取消／撤權確認與 unknown 對帳仍待 M2/M3。UTC 時間的單調累計測試不代表已有完整 wall-time supervisor；執行期仍需監測與時計異常處理。
5. WorkItem DAG／work attempts 派工、repair roots 不可重置的修復額度、typed user questions、政策與預算 extension、archive／restore 及重啟 UI 仍待實作。
6. 真正成果登記、容器隔離、乾淨驗證、候選 lineage／發布 CAS、Windows 人工證據及真模型修復閉環尚未驗收。任何一項都不能用本批資料庫測試替代。

## 重現

在 repository 根目錄使用既有 `.venv`，不需安裝依賴：

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_runs tests.collaboration.test_mailbox tests.collaboration.test_storage -q
.venv/Scripts/python.exe -m unittest discover -s tests/collaboration -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe scripts/collaboration/m1_runtime_mutation_probe.py --output <new-empty-evidence-path>
.venv/Scripts/python.exe scripts/collaboration/m1_mutation_probe.py --output <another-new-empty-evidence-path>
```

完整框架／Web 測試需要允許本機 loopback socket。上面測試不需 GPU／Docker；不要為執行測試啟動本機模型。實作尚未提供正式 migration 指令；不可把 fixture 初始化用於既有資料庫。

證據根目錄：`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`。本批索引為 `collaboration-m1-runtime-index.json`，連同各檔 SHA-256 記錄最終未提交 tree、平台、命令、實際結果及限制。candidate／plan／environment hash 不適用於這批領域 fixture；沒有可冒填為正式執行的 TeamRun。測試會建立臨時 run UUID，但不是已執行 Agent 的產品 run。
