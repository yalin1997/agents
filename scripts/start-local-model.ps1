param(
    [string]$InstallRoot = 'D:\LocalLLM',
    [int]$Port = 8080,
    [int]$ContextSize = 16384
)
$ErrorActionPreference = 'Stop'
$baseUrl = "http://127.0.0.1:$Port"
try {
    $models = Invoke-RestMethod "$baseUrl/v1/models" -TimeoutSec 3
    if ('qwen3.8-27b' -in $models.data.id) {
        Write-Host "Local model already running at $baseUrl/v1"
        return
    }
    throw "Port $Port is serving another model. Choose a different port."
} catch {
    # Windows PowerShell 5.1 may not have System.Net.Http loaded. Compare the
    # type name instead of resolving HttpRequestException in a typed catch.
    if ($_.Exception -isnot [System.Net.WebException] -and
        $_.Exception.GetType().FullName -ne 'System.Net.Http.HttpRequestException') {
        throw
    }
}
$exe = Join-Path $InstallRoot 'llama-b11883\llama-server.exe'
$model = Join-Path $InstallRoot 'Qwen3.8-27B-UD-Q4_K_M.gguf'
if (!(Test-Path -LiteralPath $exe) -or !(Test-Path -LiteralPath $model)) {
    throw "Missing llama.cpp or model in $InstallRoot. See docs/local-llm.md."
}
$serverArgs = @(
    '-m', ('"{0}"' -f $model),
    '--alias', 'qwen3.8-27b', '--host', '127.0.0.1', '--port', "$Port",
    '-ngl', '99', '-c', "$ContextSize", '-np', '1', '--jinja',
    '--flash-attn', 'on', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
    '--reasoning-budget', '2048', '--n-predict', '4096'
)
$process = Start-Process -FilePath $exe -ArgumentList $serverArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $InstallRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $InstallRoot 'server.stderr.log')
Write-Host "Loading Qwen3.8-27B on GPU (PID $($process.Id))..."
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    if ($process.HasExited) { throw "Model server exited. Check $InstallRoot\server.stderr.log" }
    try {
        $health = Invoke-RestMethod "$baseUrl/health" -TimeoutSec 2
        if ($health.status -eq 'ok') {
            Write-Host "Ready: $baseUrl/v1 (context $ContextSize)."
            return
        }
    } catch { }
    Start-Sleep -Seconds 2
}
throw "Server is still loading. Inspect $InstallRoot\server.stderr.log before retrying."
