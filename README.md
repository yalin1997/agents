# pi_deepagent

用 Python `langchain-ai/deepagents` 實作的 Pi Coding Agent 風格 coding assistant。

## 安裝

```bash
cd C:/Users/y4lin/gemini_repo/pi_deepagent
pip install -e .
```

在專案根目錄建立 `.env`（可複製 `.env.example`），填入 API key：

```dotenv
GOOGLE_API_KEY=你的_Gemini_API_key
PI_DEEPAGENT_MODEL=google_genai:gemini-3.6-flash
```

從專案根目錄啟動 `pi-deepagent` 或 `pi-a2a`，會自動載入目前目錄的 `.env`。
既有環境變數優先，`--model` 可覆寫模型設定；不會搜尋父目錄的 `.env`。
`.env` 已排除於 Git，請勿提交真實 API key。

也可以在 PowerShell 直接設定環境變數，例如：

```bash
$env:GOOGLE_API_KEY = '...'
$env:OPENAI_API_KEY = '...'
$env:ANTHROPIC_API_KEY = '...'
```

## 使用

RTX 4090 本地 Qwen 模型的啟動與串接方式見 [本地 LLM 說明](docs/local-llm.md)。

```bash
pi-deepagent --workspace C:/Users/y4lin/gemini_repo --model google_genai:gemini-3.6-flash "列出目前專案"
```

不帶 prompt 時會進入可持續對話模式；輸入 `/exit`、`/quit` 或按 Ctrl-D 離開。若只想讀取一次輸入並結束，可使用 `--once`。

也可以用環境變數設定預設 workspace 與模型：

```bash
set PI_DEEPAGENT_WORKSPACE=C:/Users/y4lin/gemini_repo
set PI_DEEPAGENT_MODEL=google_genai:gemini-3.6-flash
pi-deepagent
```

若要啟用 Deep Agents 的本機 shell `execute` tool：

```bash
pi-deepagent --shell "執行 pytest"
```

`--shell` 使用 `LocalShellBackend`，會繼承目前程序的環境，讓 Windows 上的 `python`、`pytest`、`rg` 等命令可直接使用。這沒有 sandbox，會讓模型以目前使用者權限執行任意 shell 命令，僅建議在受控開發環境使用。

不使用 `--shell` 時仍可用 `pi_bash` 執行單一、非 shell 的命令；需要 pipe、redirect 或 `&&` 時才使用 `--shell` 提供的 `execute`。

## 組成

- `pi_deepagent.prompt`: Pi 風格 system prompt
- `pi_deepagent.tools`: `pi_read`, `pi_write`, `pi_edit`, `pi_bash` 與 workspace-bound `create_pi_tools()`
- `pi_deepagent.agent`: `create_pi_deepagent()` factory
- `pi_deepagent.cli`: 單次與互動式 CLI entrypoint
- `pi_deepagent.contracts`: framework-neutral run, event, model, and tool-call contracts
- `pi_deepagent.runtime`: shared `ApplicationService` and runtime boundary
- `pi_deepagent.providers`, `sessions`, `context`, `extensions`, `commands`: public adapter boundaries
- `pi_deepagent.capabilities`、`pi_deepagent.errors`: capability vocabulary and stable error taxonomy

M0 contract details and serialization shapes are documented in
[`docs/core-contracts.md`](docs/core-contracts.md).

## A2A 1.0 一層委派

使用官方 `a2a-sdk` 的可選整合，包含真正 HTTP/SSE、多輪反問續接、artifacts、查詢、取消與 SQLite task persistence。

```powershell
uv sync --extra a2a
# 終端一（沿用既有模型 API key／PI_DEEPAGENT_MODEL）
uv run --extra a2a pi-a2a serve --workspace .
# 終端二
uv run --extra a2a pi-a2a chat --workspace .
```

詳見 [操作與架構說明](docs/a2a.md)。本機單使用者，server固定bind loopback；不開放網路部署。

## Web 工作台

正式本機 UI 提供單一 admin 登入、任務紀錄、Agent 執行進度、停止及重試。先執行 `python -m pi_deepagent.web init-admin` 初始化密碼，再執行 `python -m pi_deepagent.web --workspace .`，開啟 `http://127.0.0.1:8765/`。沿用既有 a2a extra 的 uvicorn；詳見 [使用方式與安全邊界](docs/web-workspace.md)。

## 測試

```bash
python -m unittest discover -s tests -v
```
