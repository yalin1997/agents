# pi_deepagent

用 Python `langchain-ai/deepagents` 實作的 Pi Coding Agent 風格 coding assistant。

## 安裝

```bash
cd C:/Users/y4lin/gemini_repo/pi_deepagent
pip install -e .
```

依你要用的模型設定 API key，例如：

```bash
set GOOGLE_API_KEY=...
set OPENAI_API_KEY=...
set ANTHROPIC_API_KEY=...
```

## 使用

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

## 測試

```bash
python -m unittest discover -s tests -v
```
