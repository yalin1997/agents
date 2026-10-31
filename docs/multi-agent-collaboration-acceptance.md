# 多 Agent 協作 v0.3：驗收矩陣與證據索引

日期：2026-10-30（Asia/Taipei）。依據：[規格 v0.3](multi-agent-collaboration-spec.md)、[實作計畫](multi-agent-collaboration-plan.md)。

**2026-10-31 最新狀態：paused，只做交接，完整 AC 仍 0/31。** 續作入口為 [完整交接 prompt](multi-agent-collaboration-handoff-prompt.md)。最新 repair-limit／009 raw evidence 為 Python **437 passed（242.152 秒）**、Node **24**、打散 20、root／typed 各 10/10 變異及真 SQLite fixture；56 檔 source manifest 比對符合，但正式該批 review／index 尚缺，尚未完成矩陣的逐項證據收尾，不能提高任何整項 AC。最近完整 review／index 仍是 criteria 的 417。以下「最新／active」等表述均為其當次歷史紀錄，以此段及交接最新指示為準。Docker 已由使用者卸載，改規劃 D 磁碟重新安裝；GPU 延後至必要時再請授權，本次均未執行。真 Linux／模型／完整 UI 等尚未驗收。

2026-10-31 續作 checkpoint：[CP-20261031-work-wip](multi-agent-collaboration-checkpoint-20261031-work-wip.md)；完整要求及授權見 [需求存檔](multi-agent-collaboration-user-requirements.md)。checkpoint 後最新增量為 [WorkItem／DAG 領域審核](multi-agent-collaboration-m1-work-review.md)：最後完整 Python **371**、Node **24**、27 項打散／11 個變異，以及真 SQLite round-trip／快照；最新索引 `collaboration-m1-work-index.json`。Work 的本批領域範圍已驗證，沒有真 live Work／完整 repair generation／候選驗證，完整 AC 仍為 **0/31**，目標 active。

[草稿 HTTP 批次](multi-agent-collaboration-m1-http-review.md) 的 344、[policy-v5 checkpoint](multi-agent-collaboration-checkpoint.md) 及 work-wip checkpoint 的未驗證描述，分別是各自歷史狀態。各批綁定各自 tree／證據，不能把舊數字當成目前版本，也不能把部分領域測試提升為整項 AC pass。

最新增量為 [typed approvals 審核](multi-agent-collaboration-m1-typed-approvals-review.md)：capability ceiling／responsibility_exception、007 migration，完整 Python **394**、Node **24**、23 項打散、10/10 變異與真 SQLite probe；索引 collaboration-m1-typed-approvals-index.json。上述 Work 371 為前一 tree，整體仍 **0/31**。

目前最新為 [criteria_change 審核](multi-agent-collaboration-m1-criteria-review.md)：008、不可變目標版本／精確 admin 決議／WorkAttempt refs，完整 Python **417**、Node **24**、23 項打散、10/10＋10/10 變異及真 SQLite fixture；索引 collaboration-m1-criteria-index.json。394 是前一 tree；refs 身份檢查不等於真正 gate／publication，整體仍 **0/31**。

**目前 M0 框架合約及 M1 領域／SQLite／團隊草稿、RunAttempt／累積預算／mailbox 已部分實作並驗證；完整功能尚未驗收，AC 通過 0/31。** 下表主矩陣仍為完整 AC 的預定位置；已驗證部分另列於下方批次索引，不能提升為整項 AC 通過。使用 `C/` 表示 `pi_deepagent/collaboration/`，`T/` 表示 `tests/collaboration/`；既有 `unittest discover -s tests` 已實際發現新增 suite。可調整測試拆檔，但不得減少反例或驗證層。

狀態：未開始＝尚無實作／執行；受阻＝已確認必要外部條件不足；失敗＝已有不符合預期的執行；通過＝完整原始證據符合該 AC。部分分支通過時 AC 仍不算通過。

## AC-01–31 追蹤

| AC | 功能／階段 | 實作位置（規劃） | 測試位置與必要實際行為（規劃） | 證據 ID／必要平台 | 目前狀態與實際結果 |
|---|---|---|---|---|---|
| AC-01 | F1/F11；M1/M5 | C/contracts, service, web_adapter；web/static | T/test_teams、HTTP；UI 三位成員啟動／重載版本一致；服務拒絕 1/5 人、零/多協調者、未知欄位 | E-01；Windows browser＋SQLite＋Linux | 部分：draft service 與真 HTTP 三成員設定／編輯／重啟持久化已驗；UI／Run 啟動未開始 |
| AC-02 | F1/F10；M5 | static/app, state；C/service | JS＋browser；定向訊息、typed answers、conflict、stop/reload、375/768/desktop、keyboard/44px、focus/scroll/draft、logout late response、console | E-02；Windows browser | 未開始 |
| AC-03 | F2；M1/M2 | C/policy, execution, service | T/test_policy、HTTP／Linux；連續已授權命令、分離 install/network/apply；篡改 actor/run/member/policy/workspace 與失效能力拒絕 | E-03；SQLite＋HTTP＋真 Linux | 部分：typed capability ceiling、預設 unavailable／approve 再驗、bool／未知能力／host 欄位、current grant 與責任 work/member/attempt scope 已驗；實際 executable capability、HTTP／容器仍未驗 |
| AC-04 | F2；M1/M3 | C/policy, scheduler, execution | T/test_revocation；repo/peer/tool 注入不擴權；撤權先封新操作、確認停不合規程序、獨立成員續行 | E-04；真 graph＋Linux | 部分：只有 admin 精確 typed policy 決議、責任例外不改 PolicySpec／MemberSnapshot，remove capability 要求獨立 revoke protocol 已驗；真正撤權／停止不合規環境及注入真 runtime 未整合 |
| AC-05 | F3；M2 | C/workspace | T/test_workspace；dirty tracked＋選 untracked、秘密／特殊檔拒絕、捕捉變動、HEAD/index/原檔 bytes 保持 | E-05；真 Git＋Windows FS | 未開始 |
| AC-06 | F3；M0/M2 | C/workspace, backend | T/test_isolation；兩成員同名檔各讀自己內容、原檔不變、接入 immutable peer version 保留 local changes 並追溯 | E-06；兩個真 Linux 環境 | **受阻**；engine 已就緒，映像存放位置／拉取待核准，尚無容器測試 |
| AC-07 | F4；M0/M2 | C/execution, backend, runtime | T/test_container_tools；Python/test/原生及 pi 檔案工具同一副本，image/command/exit 可查，無 host fallback | E-07；真 Linux＋graph | **受阻**；映像存放位置／拉取待核准 |
| AC-08 | F2/F4；M2 | C/policy, workspace, backend, execution | T/test_sandbox_security；host sentinel/peer/DB/socket、traversal/symlink/reparse/archive 攻擊拒絕、未授權 network 失敗 | E-08；真 Linux＋Windows sentinel | **受阻**；映像存放位置／拉取待核准 |
| AC-09 | F4；M0/M2/M3 | C/execution, scheduler | T/test_container_lifecycle；timeout/child/background/OOM/PID/output/storage 及 engine fault；確認停止前不重用 lease、其他副本不污染 | E-09；真 Linux，故障隔離 | **受阻**；映像存放位置／拉取待核准 |
| AC-10 | F5；M3 | C/scheduler, storage | T/test_scheduler；三成員／兩 execution slots、member serial、FIFO、等待釋放、background 保留計量，model 與 execution 分開 | E-10；真 SQLite＋graph＋Linux | 未開始 |
| AC-11 | F5；M0/M3 | C/runtime, checkpoint, supervisor | T/test_framework、test_budgets；互等/互聊/耗盡程式退出；native summary 真生成、歷史回查、工具一次、內部摘要不外洩 | E-11；真 graph＋SQLite＋Linux | 未開始；僅既有單 Agent 回歸可參考 |
| AC-12 | F6；M1/M3/M5 | C/mailbox, questions, runtime, service | T/test_mailbox、test_questions＋browser/model；驗證者直接告知實作者→修改→回覆→重提；偽 sender、跨 run、dup delivery、running arrival、過期 answer | E-12；SQLite＋真 graph／模型＋browser | 部分：SQLite 直接訊息／批次領取／unknown、typed answer 原成員／WorkAttempt、過期／失效 session／停止失效已驗；真成員修復鏈與 HTTP/UI 未整合 |
| AC-13 | F7；M4 | C/integration, workspace | T/test_integration；不同檔整合、同區段雙方保留、delete/modify、rename、binary、case collision | E-13；真 Git＋跨平台路徑 | 未開始 |
| AC-14 | F7/F10；M4 | C/integration, storage, service | T/test_publication；驗證後改共同 base/policy/stop 拒絕舊 candidate；Git/journal 故障／重啟對帳、不重複發布 | E-14；Git＋SQLite＋Linux gate | 未開始 |
| AC-15 | F8；M4 | C/validation, integration | T/test_gates；A/B 個別 pass、合併 API break 實際 fail；錯 candidate/舊 plan/skipped/空 collection 不可 pass | E-15；真 Git＋乾淨 Linux | 未開始 |
| AC-16 | F1/F8；M4/M5 | C/validation；static panels | T/test_reports＋browser；offline/真容器/真模型/Windows/Linux 分別記錄；required 缺件 UI 不 all-passed | E-16；Windows＋Linux＋真模型 | 未開始 |
| AC-17 | F9；M4 | C/artifacts, workspace, service | T/test_delivery_cleanup；外部原檔變更保留、無獨占 apply 拒絕且 patch 可用；cleanup partial/retry/偽路徑不誤刪 | E-17；Windows FS＋Git＋SQLite | 未開始；依 D4 patch 政策 |
| AC-18 | F10；M3/M4 | C/service, scheduler, storage, execution, integration | T/test_recovery；命令/mail/validation/publish 中 crash/restart、未啟用 apply 明確拒絕；stop/complete race、unknown 不重跑，啟用 apply 時另需中斷 journal 案例 | E-18；SQLite＋Git＋真 graph＋Linux | 未開始；apply 不啟用時不能宣稱測過其執行路徑 |
| AC-19 | F11；M1/M5 | C/web_adapter；web/app, auth | T/test_http_auth；**每個**新路由 GET/PATCH/POST/multipart/diff/download/pagination/ledger：anonymous/nonadmin/foreign/valid/logout/expired、CSRF/Origin、unknown fields/parent ID/idempotency | E-19；真 AuthService＋SQLite＋HTTP | 部分：四個 draft 路由逐一真 HTTP 授權／CSRF／分頁／重啟／CAS／ledger已驗；其餘全部新路由及附件未實作／驗收 |
| AC-20 | F11；M1/M5 | C/storage, migrations；web audit/redactor | T/test_storage、test_migrations、test_secrets；真 SQLite concurrent/CAS/restart/舊任務相容、備份還原；植入 secrets 不入 DB/log/附件，稽核 IDs 保留 | E-20；Windows SQLite＋實際檔案 | 部分：真 SQLite 交易／舊 Web DB 相容／備份還原／秘密植入、v6→v7 保留資料／序號／FK、DDL／關聯故障完整 rollback 已驗；完整 run／稽核／附件未整合；正式 migration 未套用 |
| AC-21 | F5/F8；M4 | C/supervisor, runtime, integration, validation | T/test_repair_e2e＋真模型 scenario；初 candidate 因**實際缺陷** fail→服務證據→Orchestrator 新策略→成員修復→new candidate→required pass→publish，無人工轉貼／重派 | E-21；真模型＋graph＋Git＋SQLite＋Linux | **受阻**；GPU 模型依管理員要求 pending，容器映像亦待核准；未執行 |
| AC-22 | F5/F7；M3/M4 | C/supervisor, storage, integration | T/test_repair_limits；fingerprint/無進展/次數退出、blocker report；換人/拆子工作/lineage 重建/來源 root 更換不重置；multi-root 任一額度耗盡拒絕 | E-22；真 SQLite＋Git＋graph | 部分：服務綁定初始／child／grandchild／多來源 roots、used 不倒退、首次派工封初始計畫與舊歷史 migration 不歸零已驗；真正 repair generation／原子預留消耗／額度耗盡與 Git lineage 未實作 |
| AC-23 | F2/F5/F10；M3 | C/policy, supervisor, scheduler | T/test_repair_policy；超界只阻相依工作、獨立工作完成；unknown 不 replay；stop/revoke 後 late decision 不派工 | E-23；SQLite＋graph＋Linux | 未開始 |
| AC-24 | F5/F8；M3/M4 | C/supervisor, validation, storage | T/test_repair_events；dup events/member+validator 不重派；obsolete candidate 不喚醒；模型想跳 gate/弱化條件拒絕並稽核 | E-24；真 SQLite 競態＋graph | 部分：criteria_change 僅 admin 精確 version/hash，原條件及決議不可變、舊 refs 失配已驗；真正 failure 去重、候選／gate、protected asset 審查與模型繞過真 runtime 未整合 |
| AC-25 | F5/F10/F11；M1/M3 | C/contracts, service, checkpoint, workspace | T/test_lifecycles；definition 修改不改 snapshot、新任務無舊權限/history；failed→新成功 attempt 追加；容器移除保留定義與證據 | E-25；SQLite＋graph＋真 Linux 重建 | 部分：定義／快照／criteria 歷史不可變、首次 WorkAttempt immutable inputs／criteria refs／活動唯一鍵、停止及中斷保留舊終態、continue 新 RunAttempt／累積用量已驗；failed→新成功 WorkAttempt 真執行及容器生命週期未整合 |
| AC-26 | F3/F7；M4 | C/integration, workspace, supervisor | T/test_candidate_lineage；C+A+B→F fail→checkout F 修復 R/publication C，A+B+repair 全保留；另一無交集候選推進後 remerge/recheck、來源不重複、claim 互斥 | E-26；真 Git＋SQLite 競態＋Linux | 未開始 |
| AC-27 | F5/F8；M0/M4 | C/validation, backend, runtime, checkpoint | T/test_protected_assets、test_private_history；刪 assertion/skip/new conftest/plugin/config/scripts/lock/deps 觸發 review→新 plan→全重驗；一般修復自主；offload 不入 candidate、重建後可回查本人 | E-27；真 graph＋Git＋乾淨 Linux | 未開始 |
| AC-28 | F8/F11；M4/M5 | C/validation, artifacts, web_adapter | T/test_manual_evidence＋browser；Windows required 缺件阻發布；正確 admin_manual evidence 解除、標人工；舊版本/缺件/foreign/expired/fake Agent/超限 multipart 拒絕 | E-28；Windows admin 操作＋HTTP＋SQLite | 未開始 |
| AC-29 | F2/F4/F5/F8；M2/M3/M4 | C/execution, scheduler, validation, integration | T/test_service_actors；member 滿兩 lease，validation/regeneration 排隊公平；stop/revoke 不新啟動；unknown 不釋 lease；乾淨重建 environment hash | E-29；真 Linux＋SQLite | **受阻**；映像存放位置／拉取待核准 |
| AC-30 | F5/F9/F10；M3/M4 | C/service, supervisor, scheduler, storage | T/test_continue_archive；known/unclassified 可診斷、unknown 不 replay；continue 新 attempts／保留消耗與完成成果／舊 request 失效；不足明確 extension；archive active/unknown 409、restore 不跑 | E-30；SQLite＋graph＋Linux | 部分：新 RunAttempt／累積用量、Work 完成狀態及舊 attempt 保存、unknown／活動 work 拒絕續接、待答取消／舊回答不喚醒、額度不足拒絕已驗；完整對帳、真成果保留、新 WorkAttempt 重建及 archive/restore 未整合 |
| AC-31 | F5/F6/F10；M3/M5 | C/service, storage, mailbox, scheduler | T/test_settlement_races；complete vs message/failure/work、queued stop vs start claim 兩種順序、budget expiry 先關派工後排空；wait 計時、terminal interval 不計；人工拒絕確定退出 | E-31；SQLite 多連線 barrier＋真 runtime | 部分：queued start/stop、stop／Work 接受競態、DAG／Work revision CAS、原子搶額度、耗盡先持久關派工、晚到訊息保留、terminal 間隔不計已驗；真 runtime 完成屏障／人工拒絕未整合 |

## 每次執行必填紀錄

每個 E-xx 可有多筆追加的 execution record。只有覆蓋完整 AC 的全部必要平台／成功與失敗反例，才更新矩陣為通過。記錄不得覆寫前次失敗：

```json
{
  "evidence_id": "E-xx/<unique-execution-id>",
  "acceptance_ids": ["AC-xx"],
  "spec_version": "0.3",
  "spec_sha256": "<exact spec bytes>",
  "revision": "<git HEAD>",
  "worktree_diff_sha256": "<tracked diff + selected untracked manifest>",
  "source_manifest_sha256": "<executed source files>",
  "team_run_id": null,
  "run_attempt_id": null,
  "work_attempt_ids": [],
  "candidate_hash": null,
  "publication_base": null,
  "checkout_base": null,
  "lineage_id": null,
  "validation_plan_hash": null,
  "policy_version": null,
  "environment_hash": null,
  "image_digest": null,
  "platform": "<Windows control / Linux execution / browser>",
  "tool_versions": {},
  "model": null,
  "model_kind": "<real / controlled / not-applicable>",
  "command_or_operations": [],
  "preconditions": [],
  "expected": "<observable assertions>",
  "actual": "<observed results>",
  "result_certainty": "<known / unknown>",
  "status": "<passed / failed / blocked>",
  "started_at": "<UTC ISO8601>",
  "finished_at": "<UTC ISO8601>",
  "artifacts": [{"path": "<relative evidence file>", "sha256": "<bytes hash>"}],
  "limitations": []
}
```

不適用的識別使用 null 並說明原因；不得填假 candidate／digest。真模型紀錄保留明確可見訊息、工具結果摘要、服務事件、diff／版本及 required gate，排除憑證與私有 chain-of-thought。證據索引對每筆檔案驗 hash；必要失敗 fixture、重現輸入與環境 lock 一併交付。臨時原始 log 若不便提交，產生遮蔽且可攜的內容檔，不能只有聊天連結。

## 目前已有的基準證據（不計新 AC）

證據目錄：`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`。

| ID | revision／平台／操作 | 預期 | 實際與證據 | 範圍限制 |
|---|---|---|---|---|
| B-PY | `6bd537a`；Windows Python 3.13.14；`.venv/Scripts/python.exe -u -m unittest discover -s tests -q`，允許 loopback socket | 既有全部 unittest 通過 | 188 項、44.506 秒、OK、程序 exit 0；`collaboration-baseline-python.log` | 可控模型；沒有團隊 suite；不證明真模型／容器 |
| B-JS | 同 revision；Windows Node 22.23.2；`node --test tests/ui-state.test.mjs tests/ui-app.test.mjs` | 既有前端測試通過 | 24 項、pass 24/fail 0；`collaboration-baseline-node.log` | 不等於團隊 UI 或真瀏覽器驗收 |
| P-D1 | Windows；`docker version --format '{{json .}}'`，沙箱內外 | 可用 Linux Server | Server=null，`dockerDesktopLinuxEngine` pipe 不存在 | **受阻**；未啟動／安裝 Docker/WSL |
| P-MODEL | Windows；讀既有 `.env` 的 loopback endpoint，GET `/health`，沙箱外 | 現有模型 health 成功 | WinError 10061 connection refused | **受阻**；未啟動模型，沒有模型 smoke |
| P-D3 | 本機安裝版 signatures／source | 公開 backend/middleware/checkpointer 可供 M0 spike | 有 `create_deep_agent` 公開參數、CompositeBackend/artifacts_root、BaseCheckpointSaver 同步／非同步與 pending writes 介面 | 僅靜態調查；真實團隊 graph 合約尚未驗證 |

preflight 觀察的原始工具輸出在本次工作紀錄中；正式 M0 交付時須由可重現 probe 保存成帶時間、工具版本及各操作 return code 的獨立檔案。目前不把靜態調查或未保存原始輸出的環境觀察提升為任何 AC 通過證據。

## M0 第一批實際證據（不計完整 AC 通過）

詳見 [M0 審核與重現指令](multi-agent-collaboration-m0-review.md)。程式與測試已存在：

| 實證項目 | 實作／測試位置 | 預期與實際 | 證據 |
|---|---|---|---|
| checkpoint 公開 saver 合約 | `C/checkpoint.py`、`T/test_checkpoint.py` | thread／namespace、完整版本／pending writes／歷史／取消／真 graph；已通過；服務重啟調度仍未驗收 | `collaboration-m0-tests-2.log`、coverage JSON |
| 原生私有摘要／offload、工具拒絕 | `C/middleware.py`、`T/test_framework.py` | 真 graph＋可控模型產生摘要／完整歷史，工具一次，final 不含內部摘要，偽 task 拒絕、單 Agent 不受影響；已通過 | `collaboration-m0-framework-2.log`、全 suite log |
| 唯讀 preflight | `C/preflight.py`、`T/test_preflight.py` | 真 engine 已就緒；缺 approved image 正確保持 blocked；GPU 未探測 | `collaboration-m0-preflight-engine.json` |
| 全回歸與測試可信度 | `tests/`、`scripts/collaboration/mutation_probe.py` | Windows 211 項通過；M0 23 項、checkpoint／middleware 全分支覆蓋、checkpoint 6/6 抽樣變異被抓出 | `collaboration-m0-full-python.log`、`collaboration-m0-coverage-2.json`、`collaboration-m0-mutations/results.json` |

共同 revision 仍為 `6bd537a9fbf17c790de356cbe006b97ddf1b55fb`，M0 source 為未提交新增檔案，必須配合該批 source SHA-256 manifest，不能只用 HEAD 重現。這些 probe 沒有 TeamRun／candidate／validation plan，相關識別不適用；沒有聲稱容器／真模型／Windows required-platform gate 通過。

Docker 實際資料仍在 `C:\Users\y4lin\AppData\Local\Docker\wsl\disk\docker_data.vhdx`。管理員要求先審查移至 `D:\DockerDesktop\wsl` 的方案，見 [Docker 搬移計畫](docker-storage-move-plan.md)；尚未停止三個既有容器／搬移 VHDX／拉取新映像。GPU 相依工作依管理員指示 pending。

## M1 第一批實際證據（不計完整 AC 通過）

詳見 [M1 審核與重現指令](multi-agent-collaboration-m1-review.md)。新增的 `contracts.py`、`state.py`、`storage.py`、`service.py` 與 `migrations/001_foundation.sql` 已存在；測試為 `T/test_contracts.py`、`test_state.py`、`test_storage.py`、`test_team_service.py`、`test_migration_web_compat.py`。

M0＋M1 suite **62 通過**；完整 Windows Python **250 通過**。M1 變異抽查 **9/9 killed**，保存原始個別 case log。真 SQLite／admin session 驗證授權先於 replay、交易原子性、同 ID 原結果與異 body 拒絕、兩連線 CAS、秘密遮蔽與定義版本保存；原 Web service 的真 DB 也完成加表相容與 DB backup 還原。

證據在既定根目錄的 `m1-verified-tests.log`、`m1-verified-report.json`、`m1-full-python.log`、`m1-final-mutations/`。仍需搭配 source/migration SHA-256 manifest，HEAD 未包含新增未提交檔案。這批沒有正式 TeamRun／candidate／plan，也沒有真 Docker／GPU 成員執行，相關欄位不適用，不能冒填。

尚無新增 HTTP／UI 入口；Token 驗證是直接 service 測試，不能替代 AC-19 逐路由矩陣。TeamRun 的全域 lease／累積預算／mailbox／停止恢復仍待下一批。正式 migration 未套用，Docker 搬移／映像拉取／GPU pending 邊界維持。

## M1 第二批實際證據（2026-10-31；不計完整 AC 通過）

詳見 [RunAttempt／預算／mailbox 審核](multi-agent-collaboration-m1-runtime-review.md)。程式為 `C/runs.py`、`budgets.py`、`mailbox.py`、版本化 `storage.py` 及 `migrations/002`、`003`；主要測試為 `T/test_runs.py`、`test_mailbox.py`、`test_storage.py`。

最終 collaboration **100 通過**，Windows 完整 Python **288 通過**，Node **24 通過**。新抽樣變異 **10/10 killed**、原基礎變異重跑 **9/9 killed**；run／mailbox 的 37 項打散順序 **37 通過**。證據為 `m1-runtime-final-tests.log`、`m1-runtime-final-full-python.log`、`m1-runtime-node.log`、`m1-runtime-final-coverage.json`、`m1-runtime-final-mutations/`、`m1-runtime-foundation-mutations/`、`m1-runtime-shuffled.*`，均在既定私有證據根目錄。`collaboration-m1-runtime-index.json` 保存命令、平台、預期／實際與最終 source/migration/evidence SHA-256。

這批只有內部持久化方法；未接 HTTP、graph、外部資源 leases／journal、既有單 Agent 反向活動鎖或完整結算。WorkItem／WorkAttempt／result refs schema 不等於已完成派工／整合／驗證。沒有正式執行的 candidate／plan／environment hash；測試內暫存 UUID 不冒充產品執行證據。仍不得宣稱任何完整 AC 通過。

Docker 搬移只備妥可審查方案，尚未執行；2026-10-30 覆核 Desktop 為 paused，未解除暫停。真容器與映像 gate 保持受阻；GPU 仍依管理員指示 pending。

## M1 第三批實際證據（2026-10-31；不計完整 AC 通過）

詳見 [typed user question 審核](multi-agent-collaboration-m1-questions-review.md)。新增 `C/access.py`、`questions.py`、`migrations/004_user_questions.sql`、`T/test_questions.py`；修改 mailbox 原子批次以同序號處理答案，run 關閉時取消未決問題，草稿服務共用 admin boundary。

協作 suite **121 通過**，完整 Windows Python **309 通過**；58 個 run／mailbox／question 測試打散順序亦通過。抽樣變異 **17/17＋9/9 killed**，沒有 source drift／import error／empty suite 假計通過。證據：`m1-questions-tests.log`、`m1-questions-full-python.log`、`m1-questions-coverage.json`、`m1-questions-shuffled.*`、兩個 mutation 目錄及 `collaboration-m1-questions-index.json`。所有紀錄附最終未提交 source／migration／evidence SHA-256。

型別化回答只保存資料及指向原 WorkAttempt 的投遞，不提供能力或驗收成功。這批沒有 HTTP/UI、真 graph 待答恢復、外部 lease 釋放或 Orchestrator 消費拒絕／逾時的證據。非程式驗收、ApprovalRequest、manual-validation 仍未實作。正式 migration、Docker 搬移、映像拉取及 GPU 皆未執行；整體保持 **0/31 AC 通過**。

## M1 第四批／本次 checkpoint（2026-10-31；不計完整 AC 通過）

最新狀態及政策語意保存在 [checkpoint 第 5–6 節](multi-agent-collaboration-checkpoint.md)。新增 `C/policies.py`、`migrations/005_policy_versions.sql`、`T/test_policies.py`；run 初始政策與換版保存不可變歷史，admin 精確延長 model_calls／messages／wall_seconds 總額，核准／拒絕／過期／舊版本／父子歸屬使用交易與 CAS。舊 grant 不能新派工，但可信任 coordinator 可用精確歷史身分結算原先已知操作與回合；不清 unknown、不改舊 attempt、不自動續跑。

協作 suite **142 通過、26.831 秒**；完整 Windows Python **330 通過、69.588 秒**；抽樣變異 **26/26＋9/9 killed**，0 fail／skip。真 v4 schema 與資料升級／備份保存、approve-vs-deny、SQL 故障及交易內 revision 改動的 CAS 均有測試。policy 行／分支覆蓋為 148/154、47/52；仍有未覆蓋防守分支。**本批尚未另外執行 shuffled 79 項，不標通過。**

原始證據為既定 evidence 根目錄的 `m1-policies-tests.log`、`m1-policies-full-python.log`、`m1-policies-coverage.json`、`m1-policies-mutations/`、`m1-policies-foundation-mutations/`；`collaboration-m1-policies-index.json` 保存最終 source／migration／documentation／evidence SHA-256 與實際結果。沒有正式 Agent run、candidate／plan／environment hash，不冒填。

這批只有內部 admin budget_extension，不支援其他四種 ApprovalRequest、repair-root extension 或模型核准提案，也不提供 HTTP/UI、命令／網路／發布權限或實際程序撤權。正式 migration、Docker 搬移／映像拉取、GPU、真模型及容器驗收皆未執行。M0/M1 未完成；原始最終範圍完整保留。

## M1 第五批：草稿 HTTP（2026-10-31；不計完整 AC 通過）

詳見 [HTTP 審核](multi-agent-collaboration-m1-http-review.md)。新增 `C/web_adapter.py`、`T/test_team_http.py`、兩個 probe；修改原有 `web/app.py`、`web/__main__.py` 及 draft service 的讀取遮蔽。只有明確 `--collaboration` 且已有正確 schema 才啟用四個草稿路由，不自動 migration，不建立 TeamRun 或冒充程序停止。

最終完整 Python **344 通過、115.524 秒**；新 HTTP／CLI＋既有 WebApp＋draft service **40 通過**，Node **24 通過**；新 14 項打散順序通過。抽樣變異 **8/8＋9/9 killed**。首個變異 baseline 的 CLI 編碼失敗保留且不計分，修正後 baseline 與變異重跑。三成員實際 HTTP probe 保存 request ID、team ID、revision、預期／實際 status、audit、SQLite，server 已停止；沒有 credentials／session／CSRF 存檔。

證據：`m1-team-http-final-full-python.log`、`m1-team-http-covered-tests.log`、`m1-team-http-coverage.json`、`m1-team-http-node.log`、`m1-team-http-shuffled.*`、`m1-team-http-roundtrip-3members/`、`m1-team-http-mutations-final/`、`m1-team-http-foundation-mutations/`；最新 tree／tracked diff／evidence SHA-256 為 `collaboration-m1-team-http-index.json`。本批沒有真正 Agent run／candidate／plan，不填假 hash。

HTTP 草稿不是整隊執行或 UI 驗收；M0/M1 尚未完成、其餘路由與全部 Linux／真模型／候選 gate／人工平台／附件仍未驗收。舊 checkpoint 為歷史快照，不以本批變更改寫舊證據。

## 最終交付檢查（全部尚未完成）

- F1–F11 實作與完整 code/spec review；每個 AC 全分支及必要平台有 execution record。
- 真 graph、SQLite、Git、Linux 容器與實際設定模型自主修復閉環。
- 全新 HTTP 路由逐一授權矩陣、CSRF/Origin、parent membership、multipart／附件秘密測試。
- 並行、重複／過期、停止／撤權／重啟／unknown、lineage／CAS／repair root 累積額度證據。
- required protected review、新 plan 重驗、Windows manual 證據與 UI 真瀏覽器／mobile／keyboard。
- 修改後 Windows 完整 Python／Node、CLI／A2A 回歸；Python 測試可信度審核。
- 設定／啟動／停止／續接／對帳／保留與清理文件；schema／migration、備份／回復說明及實測。
- 重新以 spec 每條規範核對最終程式與證據；沒有未處理阻擋缺陷。未滿足前不標正式可驗收。
