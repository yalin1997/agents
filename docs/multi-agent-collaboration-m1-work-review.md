# M1 第六批：WorkItem／DAG 與首次 WorkAttempt

日期：2026-10-31。這批是可信任 coordinator 使用的持久化領域服務，沒有新增 live runner 或對外執行路由。完整 AC 仍為 **0/31**；真容器／模型與 M0 gate 尚未完成。GPU pending、Docker 搬移／精確 image pull 仍待確認，沒有新增依賴、正式 migration、commit／push 或部署。

## 實作位置與合約

- `collaboration/work.py`：服務產生 PlanningContext、綁定目前 RunAttempt／policy／grant 的 Orchestrator；模型業務輸入僅 goal／criteria／dependencies，不能傳 actor、parent、root、required。建立／修改依賴在真 SQLite 同交易完成 DAG 檢查、revision CAS、event 與 request ledger；重複回原結果，異 body 拒絕。
- `migrations/006_work_planning.sql`：initial plan gate、server-issued contexts、DAG edges、root counter／完整 root 集合、來源追溯。首次 WorkAttempt 原子封閉初始規劃及撤銷初始 contexts，continue 不重新開啟。root identity／used 不倒退，mapping／origins 及目標不可任意改寫。
- `runs.py`：準備 Run 時建立 gate；停止排空目前已表示的 budget／MemberTurn 後，取消非終態邏輯 WorkAttempt／WorkItem，保留已完成成果及舊終態；中斷保留 unknown、舊 attempt 與活動 fence。continue 拒絕活動／未知 work 或 turn，只將未完成必要 WorkItem 恢復 pending，不改舊 WorkAttempt。
- `state.py`：未開始的 ready 工作重新加上未完成依賴時可回 pending，符合服務重新計算 readiness。repair_pending 的依賴更新保留 repair_pending，不能藉改 DAG 繞過修復計畫。
- `tests/collaboration/test_work.py`：27 項真 SQLite／AuthService fixtures、多連線及真 v5→v6 migration；`scripts/collaboration/work_mutation_probe.py` 為記憶體變異抽查，實際執行 SQLite，不改磁碟產品來源。

初始工作建立自己的服務 root。普通 child context 只能由 coordinator 根據可信任 source work 建立，繼承全部 root 集合與來源；grandchild、多來源 union 不建立新的計數器，也不把追溯關係自動變成阻塞 DAG 邊。這不是已完成的 repair plan／generation；M3 必須從持久 failure／candidate 證據推導修復 scope，不能把能任意選 source 的 factory 暴露為模型工具。

首次派工只建立 queued WorkAttempt，固定 assignee、goal／criteria／dependencies／root IDs／policy snapshot；有任一舊 attempt 就拒絕首次派工 API，須走尚待整合的 repair／continue protocol。沒有執行命令或宣稱 gate 成功。有效 binding 也不能跨 run／workspace／member，context nonce 及完整 binding 必須符合持久記錄。

輸入寫入時遮蔽已知秘密，讀取及 ledger replay 再遮蔽晚註冊秘密；不改寫 immutable ledger／歷史 bytes。Work 的 required 固定由服務設定，沒有讓模型降為 optional 或修改驗收目標的路徑。

## Migration、資料保留與回復

006 只在暫存測試 DB 套用。開啟 collaboration store／Web CLI 不自動升級；HTTP opt-in 對 v5 DB 會拒絕，須先經正式 migration 審查／停服務／備份及明確授權。本批沒有操作正式資料。

乾淨 v5 資料可 backfill roots／gate，保留所有原資料及 v5 備份。v5 若已有 `number > 1` 的 WorkAttempt 或 `repairs_work_item_id`，因舊 schema 沒有可證明 consumed／reserved 的修復帳本，006 以 `unaccounted_repair_history` 明確拒絕，不將用量初始化為零。測試確認備份保存、舊 DB 仍為 v5、沒有半套新表。這類資料需要另行審查的對帳及 migration，不能人工刪歷史／重設計數來通過。

root／origin 的 SQL delete guard 只為未來受控 purge 保留條件：team archived、run 已終態且活動 fence 已釋放；本批沒有 purge API 或刪除程序。不能因容器移除刪除歷史。

回復產品啟動可省略 `--collaboration`，保留資料；若要還原 DB，先停所有寫入、核對精確備份／目標及版本，依審查步驟還原，不能將舊備份與新寫入混合。本批未執行正式 rollback，亦沒有提供未審查的降版 SQL。

## 驗證範圍與可信度

27 項測試涵蓋：create／immutable snapshot／replay／禁止 optional／指定 root；真正 Orchestrator／精確 context／current grant／policy；寫入、get、三種 mutation replay 的秘密遮蔽；自環／間接環／跨 run／workspace；未完成依賴；多連線 create／assign 去重、反向 DAG 更新及同 work CAS；initial gate 跨 continue；child／grandchild／multi-root 不歸零；SQL monotonic／immutable；wall 到期先持久 stop intent；換 grant 的 unknown 阻擋；stop／work 接受競態；停止及中斷 lifecycle；event／ledger／root 原子回滾；transaction 內 revision 改變的 attempt CAS；v5 備份、backfill 與舊修復歷史拒絕。

測試中的 completed／succeeded 與舊 v5 資料由明確可信任 fixture 寫入，僅驗證持久化規則，不能算真 graph／required gate／模型成功。沒有 mock SQLite 或 mock Docker 成功結果，沒有新增 skip／xfail／exclude／pragma 或安裝依賴。

第一輪 80 項的四個 subtest failure 是新測試將既有錯誤碼 `invalid_fields` 寫成 `unknown_fields`；修正測試為精確既有合約，沒有放寬拒絕條件。之後 83 項及首次完整 369 項通過；最後另外補兩個 migration／undrained continuation 反例，因此最終版本必須以後續 371 項結果為準，不能套用較早數字。

| 執行 | 已取得結果／證據 |
|---|---|
| 最後 Work 子集 | 27 passed，12.616 秒；`m1-work-final-focused.log` |
| 前一版本領域 coverage | 83 passed，41.478 秒；`m1-work-covered-tests.log`，後兩項及後續防線不包含於此 |
| 前一版本完整回歸 | 369 passed，193.731 秒；`m1-work-full-python.log` |
| Node 既有 UI | 24 passed，0 fail／skip；`m1-work-node.log`，不是 browser／mobile 驗收 |
| 最終全回歸／branch coverage | **371 passed，201.715 秒，0 fail／skip**；`m1-work-final-full-python.log`／`m1-work-final-coverage.json` |
| 最終順序打散 | **27 passed，12.829 秒，seed 1043**；`m1-work-shuffled.json`／`.log` |
| 最終記憶體變異 | **11/11 killed，0 survivor／infrastructure error**；`m1-work-mutations-final/`。先前 10/10 僅對應新增兩項反例之前版本 |
| 真 SQLite round-trip | **4 項領域檢查通過**；`m1-work-planning-roundtrip-final/record.json`／`tasks.sqlite`，來源為 `m1-work-planning-probe.py`；沒有 graph／container 執行 |

每個 mutant 必須完成全部 27 tests、無 skip／import／setup／teardown 問題。來源唯一性不符或 child 未完成不能算 killed；若移除服務 guard 後由真 SQL unique／immutable 防線引發行為測試錯誤，會保留原始 trace，不混同 infrastructure failure。migration mutant 只替換記憶體讀取的 SQL bytes，真正 SQLite 執行、migration checksum 及備份流程仍保留；不改磁碟 SQL 或偽造成功結果。

最終 coverage（行／分支分開）：Work **199/202（98.5%）／57/62（91.9%）**，Run **250/263（95.1%）／66/82（80.5%）**，state **119/120（99.2%）／39/40（97.5%）**，沒有 exclude／omit／pragma。Work 缺口為 PlanningContext 非 binding 型別、`_context` 非 context 防守、資料庫 gate 關閉但 context 未撤銷的不一致防守，以及內部 authorization callback 的 exit 分支；Run 仍有 service-actor／invalid binding、部分不存在／stale attempt、防守排空及政策分支缺口，未用無意義案例灌滿數字。

依 unit-test-trust-review，對這個 SQLite／領域範圍採**中等信任**：真多連線、SQL failure／CAS、備份回滾及 11 個變異有實際反例；最後版本獨立子集、打散順序、mutation baseline 及全回歸均通過。`old_attempt_bypass` mutant 移除 guard 後由真 SQLite 的 `(work_id,number)` unique 防線拒絕，行為測試因預期結構化 domain error 而失敗；不是成功建立第二次初始 attempt，也不是 setup 失敗。其餘 mutant 有 assertion failure，DAG 自環另觸發 SQL check。完整執行安全性仍不可由領域 coverage 推論；沒有新增 CI coverage threshold、真執行整合仍待驗證。

## 尚未實作／驗證

- repair generation／failure 去重／多 root 原子預留消耗／無進展退出、repair-root extension、候選 lineage／publication 及 required checks；counter 表存在不算 AC-22 通過。
- explicit continue 的新 WorkAttempt 重建及完整對帳。未知 WorkAttempt 終態保持 immutable，目前拒絕續跑，未實作持久 reconciliation resolution；不可任意改 outcome 解除。
- 真 execution／container／journal 排空與 revoke。`finish_stopping` 仍是 record-only coordinator API，未接外部 lease／journal，不能暴露成真停止入口，也不能把 workflow cancelled 當成證明主機／容器副作用未發生。
- HTTP／UI Work 操作與逐路由授權、其他 approvals／非程式驗收、完整 legacy 反向 fence、M0 真 Linux gate、Windows 手動證據與真模型閉環。

## 可重現命令

在 repository 根目錄，以現有 `.venv`，輸出使用新的檔名／空目錄，不覆寫舊紀錄：

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_work -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe scripts/collaboration/work_mutation_probe.py --output <new-empty-directory>
```

完整 graph／HTTP 回歸需要允許本機 loopback socket，不啟動真 provider／GPU 或 Docker。證據根目錄維持 `C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`。真 SQLite probe 可在 repo 根目錄執行 `.venv/Scripts/python.exe <evidence-root>/m1-work-planning-probe.py --output <new-empty-directory>`，重建新的 IDs／快照；快照只作證據，不得作正式 DB。初次 inline probe 的 `m1-work-planning-roundtrip/` 另保留，不覆寫。

產品來源先存 `m1-work-final-source-manifest.json`，最終核對未變後建立 `collaboration-m1-work-index.json`，綁定 source／文件／證據、revision／平台及適用 IDs。先前 checkpoint／index 保留其當時 tree，不改寫舊 pass。全 suite 中預期的 A2A provider／timeout 故障及 CLI usage 訊息不是失敗計數，以完整 unittest 終態及 exit code 判斷。
