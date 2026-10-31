# M1 第八批：criteria_change 與不可變目標版本

日期：2026-10-31。本批已實作及驗證目標版本的 SQLite／admin 合約，**完整 AC 仍為 0/31，M0／M1 未全部完成**。沒有新增對外路由、live runner、容器／模型、正式 migration、依賴／CI、commit／push 或部署。

## 實作及權限

- `collaboration/criteria.py`：目標文件、canonical hash、版本 reference、精確 target、追加版本、WorkAttempt refs 與身份相等檢查。這些是已授權 coordinator 的內部 helper，不是 completion／執行／驗證授權。
- `migrations/008_criteria_versions.sql`：保留原 TeamRun.config_json、MemberSnapshot、WorkItem.goal／criteria_json，另建 immutable criteria_versions。新 run／work 產生 initial 版本，既有資料 backfill 標 migration；後續 approval 追加，SQL 限制 target／sequence／approval 身分，不能改寫或在活動 run 刪除歷史。
- `policies.py`：admin 的 criteria_change validator、精確 TeamRun／WorkItem 目標、old version/hash、new 完整 goal/criteria、reason／expiry、policy/run/work CAS、deny／stale／stop、歷史分頁及秘密遮蔽。模型不能呼叫 admin 決議取代授權。
- `runs.py`／`work.py`：讀取目前有效目標，原始設定保留。WorkAttempt 新輸入包含 `criteria_refs.team_criteria`／`work_criteria` 的 version/hash；既有輸入及終態不改寫。

請求 new 必須是完整 goal＋至少一個文字 criterion，文件上限 128 KiB。old 建議傳 `{version, hash}`，服務從不可變歷史解析及保存舊值；如果提供完整舊文字，必須與其 canonical hash 符合。允許 admin 明確替換／移除條件，原條件與決議仍保留，不能把功能縮成只准加條件。模型／普通訊息無法改既有條件、required、能力或預算。

TeamRun／WorkItem 的目標分開版本化。核准新增 policy version，舊 grant 不自行升級；work amendment 另推進 work revision。原 config、MemberSnapshot、WorkAttempt inputs、root、累計用量不重設。completed／failed／cancelled／interrupted Work 不直接改目標；必要時先明確 continue，或以保留來源／root 的關聯後續工作承接新增需求，不能改寫既有成功成果。

舊 refs 與新目標失配只能證明版本身份不符，**不能證明真正 required gate／candidate／publication 已執行或已阻擋**。新目標不把舊 pass 變成新 pass、不讓模型決定完成；M3／M4 還須消費 criteria_changed、處理舊 work／turn、重建適當 attempt、重驗所有所需條件與 plan，再以真正完成屏障結算。現有 queued attempt 不因 amendment 自動重跑或轉成功。

## 秘密、去重與歷史

同 request／同 body 回原結果，異 body 拒絕；新決議不重用舊 policy。動態 admission 放在 ledger replay 之後，auth／resource 仍先檢查。write／history／replay 遮蔽已知秘密；若 pending new 文件中的文字後來被登記為秘密，approve 將該請求 superseded 並要求新的已遮蔽請求，不把已知秘密複製成新版本。

舊歷史若在最初寫入時尚非已知秘密，仍保持原 bytes；新 request 的 old display 再遮蔽，不複製已知秘密。hash 永遠參照原不可變文件，不是重新計算已遮蔽顯示文字的 hash；UI 應回傳 version/hash reference，不能用顯示文字推導新的 authority。

## Migration 與回復

008 為 additive 表／triggers，不更改 001–007 checksum，無需註冊新的 FK rebuild。普通連線 foreign_keys=ON。真 v7 schema／資料測試確認 backfill、原 WorkAttempt inputs bytes、history／root／設定及備份保留；沒有猜測或回填舊 attempt 的 refs，缺少 refs 不算符合目前身份。DDL 故障後仍是完整 v7，無半套新表。

正式 DB migration 仍須審查、停完整服務、備份及明確核准；本批只用暫存 DB。開啟 store／Web CLI 不自動升級；v7 正式 DB 未核准升級前不能啟用新 collaboration schema。省略 `--collaboration` 可回原單 Agent 模式而保留資料；還原 DB 須停寫入、核對精確備份／版本並另行審查，不能合併舊備份與新寫入。沒有執行正式 rollback。

## 實際驗證與可信度

新增 `tests/collaboration/test_criteria.py` **23 項**，涵蓋 canonical Unicode hash、team/work 分離、原條件保留及 admin replacement、偽 hash/version／非法文件、immutable inputs、舊 refs 失配、新 attempt、replay／CAS／並行 approve-deny、stale／expiry／stop、terminal work、未登入／nonadmin／有效 admin／expired／logout／operation／workspace、分頁、SQL immutability、late secrets、大型舊條件 reference、v7 migration／backup／DDL rollback。

| 執行 | 最終結果／原始證據 |
|---|---|
| 初次修正後的領域子集 | 120 passed，59.345 秒；m1-criteria-first-tests.log，後續新增的秘密子案例不包含於該輪 |
| 最終 focused＋foreign mailbox case | **24 passed，10.859 秒**；m1-criteria-final-focused-2.log |
| 最終完整 Windows Python／coverage | **417 passed，227.936 秒，0 fail／skip**；m1-criteria-final-full-python-2.log、m1-criteria-final-coverage.json |
| Node 既有 UI | **24 passed，0 fail／skip**；m1-criteria-node.log，不是真 browser／mobile 驗收 |
| 新 suite 打散 | **23 passed，10.327 秒，seed 1045**；m1-criteria-shuffled-final.json／.log |
| 新 criteria 變異 | **10/10 killed**；m1-criteria-mutations-final/，每個完整 23 項，無 skip／infrastructure error |
| 既有 typed approval 變異重跑 | **10/10 killed**；m1-criteria-typed-mutations/，包含已更新的 source 唯一性檢查 |
| 真 SQLite round-trip | **9 項領域檢查通過**；m1-criteria-roundtrip-final/record.json／tasks.sqlite、m1-criteria-probe.py |

Probe 中的非零 model/message/wall／repair 用量為明確 SQL fixture，不是實測模型或修復程序計量。它證明目標變更沒有把已存用量歸零；真 provider、容器 metering、repair generation／reservation 仍未驗收。snapshot 只作證據，不得當正式 DB 或拿來重啟未決操作。

Coverage 行／分支分開：criteria **62/64（96.9%）／23/26（88.5%）**；policies **302/313（96.5%）／132/144（91.7%）**；work **201/204（98.5%）／57/62（91.9%）**；runs **249/264（94.3%）／63/82（76.8%）**。沒有 omit／exclude／pragma／skip／xfail 或新依賴／CI threshold。缺口包含不完整 old text、有效完整 old echo 的部分路徑、missing criteria 防守、少數 ctor／stored-kind／CAS／非 member actor、Run 既有 defensive／競態另一路徑；不套用舊批較高數字。

依 unit-test-trust-review 對本 SQLite／領域範圍採**中等信任**：真資料／多連線、故障回滾、獨立與打散及全回歸有證據；新 10 個變異涵蓋 baseline、空條件、hash、目前版本、work target、舊 refs、snapshot、work CAS、讀取遮蔽與 late-secret admission。harness 要求完整 marker、固定 23 項、source 唯一性及無 skip／import／setup／teardown failure；baseline 失敗不計分。

以下失敗全部保留，不能算通過：m1-criteria-initial-regression.log 的 97 項／3 fail（DTO provenance、fault injection 錯取最後 migration、inputs 新欄位）；m1-criteria-mutations/baseline.log 及 m1-criteria-shuffled.log 的新子案例 stale binding；第一輪 full 417／2 errors，m1-criteria-final-full-python.log（同一 binding 問題及既有 mailbox 的空 criteria fixture）。修正採服務重綁、fault injection 固定第 7 版並檢查唯一 marker、foreign fixture 使用有效 criterion、inputs 精確 refs 斷言；未放寬授權／schema，沒有刪測試。

## 證據與重現

evidence 根目錄：`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`。最終 source 綁定 `m1-criteria-final-source-manifest-2.json`，本批總索引 `collaboration-m1-criteria-index.json`；第一輪 attempted-final source／日誌保留為歷史。Work／typed mutation probe 的 source marker 已隨有效目標讀取及 policy admission 更新；不能把舊 source-drift／失敗算 killed。

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_criteria -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe scripts/collaboration/criteria_mutation_probe.py --output <new-empty-directory>
.venv/Scripts/python.exe <evidence-root>/m1-criteria-probe.py --seed-accounting --output <another-new-empty-directory>
```

完整 graph／HTTP suite 需要允許本機 loopback，不使用真 provider／GPU／Docker。保留新輸出，不覆寫舊失敗／成功。M0 真 Linux、validation_plan_change／required assets／actual gates、非程式驗收、修復／對帳、HTTP／UI 與真模型閉環仍待完成；Docker 停機／搬移問題仍待答，GPU pending。沒有縮小原始範圍或新增整項 AC pass。
