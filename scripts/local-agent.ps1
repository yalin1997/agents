param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AgentArguments
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot 'start-local-model.ps1')
if (!$?) { throw 'Local model could not be started.' }
# Child process only: preserve the caller's provider settings and API keys.
$previous = @{}
$settings = @{
    PYTHONIOENCODING = 'utf-8'
    OPENAI_API_BASE = 'http://127.0.0.1:8080/v1'
    OPENAI_BASE_URL = 'http://127.0.0.1:8080/v1'
    OPENAI_API_KEY = 'local-no-key-required'
    PI_DEEPAGENT_MODEL = 'openai:qwen3.8-27b'
}
try {
    foreach ($name in $settings.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m pi_deepagent.cli @AgentArguments
    $agentExitCode = $LASTEXITCODE
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
exit $agentExitCode
