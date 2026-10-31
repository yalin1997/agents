# M1 第七批：能力上限與責任範圍例外

日期：2026-10-31。本批領域／SQLite 合約已實作與驗證；**M0／M1 未全部完成、完整 AC 仍 0/31**。沒有新對外路由、實際命令授權、容器／GPU 執行、正式 migration、依賴／CI、commit／push 或部署。

## 合約與實作位置

`collaboration/policies.py` 沿用真 AuthService／AdminAccess、父子資源、先授權再 replay、transaction／CAS、immutable policy／approval ledger，除 budget_extension 新增兩種 validator：

| 種類 | 輸入與結果 |
|---|---|
| capability | old／new 為相同 key 的明確 bool：execute、dependency_install、network、publish。只增加該 TeamRun 上限，不捆綁其他能力；移除回 revocation_required，須另走未來撤權協定。host／push／deploy／env 欄位拒絕 |
| responsibility_exception | 精確同 run member_id／work_id；old／new 為 paths，只擴張並保留原責任。literal prefix：src 包含 src/a.py、不含 src2/a.py；拒絕 glob／traversal／host path／重複／過大輸入。新增 immutable policy version 與 work／member／RunAttempt 綁定記錄 |

runtime 支援集合由可信任 constructor 提供，**預設為空**；新請求與實際 approve 都檢查。測試的 support 是 fixture metadata，不是 Docker preflight 證據。M2 必須從實際已驗證 backend 產生支援集合，並在每次 executable grant 檢查；目前沒有這條 live 路徑。

責任例外只影響協作提交規則，不改 mount、強制檔案政策、秘密／服務／隊友資料或主機權限。`covers_responsibility` 是可信任 submission coordinator 內部檢查，驗 current grant／policy／run、unknown actor 及精確 work／member／attempt，不能取代 immutable diff manifest、private workspace、強制檔案政策或驗證／發布 gate。continue 不沿用舊 attempt 的例外。

核准不改 MemberSnapshot、原設定、舊 attempt、目標／criteria、used／reserved，不啟動工作。新操作仍須 current-policy grant，既有精確歷史結算限制保留。登出撤銷互動存取，預授權背景 binding 不因此失效。

動態 support／敏感路徑 admission 在新 mutation 交易中、ledger replay 之後執行，已接受同 ID 請求在環境改變後仍回原結果；異 body 拒絕。已知秘密寫入遮蔽、晚註冊秘密 replay／history 再遮蔽，歷史 bytes 不改。

## SQL 007、備份與回復

`migrations/007_typed_approvals.sql` 保留 001–006 checksum，只重建 collaboration 的 policies／approval_requests／approval_decisions 三表以擴充 CHECK，保存 rows／IDs／revision／status／ledger／序號與 immutable triggers，新增 responsibility_versions。舊 tasks／admin 憑證不重寫。schema 預留五類型不代表全部實作；runtime 仍拒絕未有 validator 的 validation_plan_change／criteria_change。

實測 SQLite **3.53.1**。依 [SQLite 官方 table-rebuild 流程](https://www.sqlite.org/lang_altertable.html#making_other_kinds_of_table_schema_changes)，建立新表／複製、刪舊表、rename 新表、重建 index／trigger，不使用 writable_schema。`storage.py` 只對註冊 rebuild 7，在 BEGIN **之前**暫停該管理連線 FK enforcement，commit 前 foreign_key_check，finally 恢復；普通服務連線 foreign_keys=ON。正式套用仍須完整停服務、備份、審查及明確授權，本批只用暫存 DB。

真 v6 schema 測試保留 approved／pending、policy history、request ledger、備份及 high-water 700，升級後新請求 701；空 approval 表但 high-water 700 的另行 probe 也確認不歸零。fixture 複製已 materialized rows 到 v6 時暫停 root 自動建立 trigger、複製後恢復，避免重複建立；這不是產品 migration 步驟。

drop 後注入 SQL 錯誤、FK 暫停期間注入非法參照，兩者均經真 SQLite 驗證 rollback 到 v6、備份完整、無半套新表。只替換記憶體 SQL bytes，沒有 mock SQLite 成功。

`--collaboration` 要求精確 schema／checksum，未核准升級的 v6 正式 DB 拒絕啟用，不自動 migration。省略 flag 可回原單 Agent 模式並保留團隊資料；資料還原須停寫入、核對精確備份／版本並另外審查，不合併舊備份與新寫入。本批未執行正式還原／降版。

## 驗證及可信度

新增 `tests/collaboration/test_typed_approvals.py` **23 項**；既有 invalid-kind 測試改為 capability 的不合法 budget 欄位回 unsupported_capability_target，未知種類另保留拒絕，沒有放寬測試。

測試包括 capability 分離／預設拒絕／approve 再檢查、bool／host／revocation、replay、責任 work/member/attempt/prefix／原範圍、foreign targets、continue、terminal／stale、deny／expiry／stop、未登入／操作／workspace／logout／binding／unknown、背景登出語意、晚註冊秘密、並行決議、policy CAS rollback、SQL immutable、v6 migration／backup／序號／DDL／FK 失敗。既有同一服務邊界另涵蓋 nonadmin／expired session；沒有新 HTTP 入口可由此宣稱逐路由驗收。

| 執行 | 實際結果／證據（evidence 根目錄下） |
|---|---|
| 領域／policy／storage／work 子集 | **81 passed，40.297 秒**；m1-typed-covered-tests.log |
| 最終完整 Windows Python／branch coverage | **394 passed，224.647 秒，0 fail／skip**；m1-typed-final-full-python.log、m1-typed-final-coverage.json |
| Node 既有 UI | **24 passed，0 fail／skip**；m1-typed-node.log，非 browser／mobile 驗收 |
| 順序打散 | **23 passed，11.120 秒，seed 1044**；m1-typed-shuffled.json／.log |
| 最終變異 | **10/10 killed**；m1-typed-mutations-final/，各完整 23 項，無 skip／infrastructure error |
| Harness 負控制 | 故意 setUp assertion 失敗，確認 infrastructure_errors=1、不算 killed；m1-typed-harness-negative-control.json |
| 真 SQLite／admin round-trip | **6 項領域檢查通過**，policy 1→2→3；m1-typed-roundtrip/record.json／tasks.sqlite、m1-typed-probe.py，無 graph／container |
| 空表序號 probe | high-water 700 保留；m1-typed-empty-sequence-probe.json |

Coverage 行／分支：policies **256/267（95.9%）／110/122（90.2%）**；storage **183/187（97.9%）／54/58（93.1%）**。沒有 omit／exclude／pragma、skip／xfail、新依賴或 CI threshold。缺口包含 invalid support constructor、過長 reason、找不到 approval、unsupported stored kind、terminal／CAS 防守、非 member actor、backup integrity／migration version race、找不到 request。

依 unit-test-trust-review 對本領域範圍採**中等信任**：真資料／多連線／故障回滾、打散及全回歸有證據；10 個變異涵蓋 support、baseline、work/member/attempt scope、path boundary、approve 再驗、publication CAS、migration FK、replay/admission。harness 要求 source 唯一、完整 marker、固定 23 項、無 skip／import／setup／teardown error 或 assertion。初版 harness 中間 probe 保留於 m1-typed-mutations/，最後重跑才作最終計分。完整執行安全不能由純領域 coverage 推論。

早期失敗保留：最初 policy 子集 31 項有 1 項舊 invalid-kind expectation 不適用；第一輪新 suite 53 項有 4 個 fixture error（v6 trigger 重複 materialize roots、重複 old keyword），m1-typed-first-tests.log。修正精確拒絕語意／資料準備後，才得到上述結果。

## 證據與重現

evidence 根目錄為 `C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`。`collaboration-m1-typed-approvals-index.json` 綁定 source／文件／evidence hashes、revision／平台與 fixture IDs；舊 checkpoint／index 是歷史 tree。快照只供證據，不作正式 DB 或跨重啟重播。

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_typed_approvals -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe scripts/collaboration/typed_approval_mutation_probe.py --output <new-empty-directory>
.venv/Scripts/python.exe <evidence-root>/m1-typed-probe.py --output <another-new-empty-directory>
```

完整 graph／HTTP suite 需 loopback，不啟動真 provider／GPU／Docker；每次保留新輸出，不覆寫舊證據。

## 尚未完成

executable capabilities、真正 revoke／不合規環境停止、隔離／preflight support；model 提案、waiting_approval 恢復與 Orchestrator 拒絕退出；其他 validators／repair-root extension／criteria／plan／required gates；HTTP／UI、全部平台／模型／容器 AC。enum、fixture support、policy ceilings 都不能當成實際執行保證。

GPU 仍 pending。Docker 搬移／映像拉取未執行；2026-10-31 新停機／備份詢問尚無回覆，不能視為核准。engine 狀態無法取得不代表容器已停，見搬移方案最新覆核。
