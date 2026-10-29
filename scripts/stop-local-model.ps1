[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InstallRoot = 'D:\LocalLLM',
    [ValidateRange(1, 65535)]
    [int]$Port = 8080
)
$ErrorActionPreference = 'Stop'
$exe = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'llama-b11883\llama-server.exe'))
$model = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'Qwen3.8-27B-UD-Q4_K_M.gguf'))
$modelPattern = '(?:^|\s)-m\s+"?' + [regex]::Escape($model) + '"?(?=\s|$)'
$portPattern = '(?:^|\s)--port\s+' + $Port + '(?=\s|$)'
$servers = @(Get-CimInstance Win32_Process -Filter "Name = 'llama-server.exe'" | Where-Object {
    $_.ExecutablePath -ieq $exe -and
    $_.CommandLine -imatch $modelPattern -and
    $_.CommandLine -match $portPattern
})
if ($servers.Count -eq 0) {
    Write-Host "No matching local model server is running on port $Port."
    return
}
foreach ($server in $servers) {
    # Keep a process handle so a reused PID cannot target a different process.
    $process = Get-Process -Id $server.ProcessId -ErrorAction SilentlyContinue
    if (!$process) { continue }
    try {
        $null = $process.Handle
        if ($process.HasExited -or $process.Path -ine $exe -or
            [Math]::Abs(($process.StartTime.ToUniversalTime() - $server.CreationDate.ToUniversalTime()).TotalMilliseconds) -ge 1) {
            throw "Process $($server.ProcessId) changed; run the script again."
        }
        if ($PSCmdlet.ShouldProcess("Qwen3.8-27B server PID $($process.Id), port $Port", 'Stop')) {
            Stop-Process -InputObject $process
            if (!$process.WaitForExit(10000)) { throw "Server did not exit within 10 seconds." }
            Write-Host "Stopped local model server (PID $($process.Id)). GPU memory released."
        }
    } finally {
        $process.Dispose()
    }
}
