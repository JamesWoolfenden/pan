$repoRoot = [regex]::Escape((Resolve-Path (Join-Path $PSScriptRoot '..')).Path)
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -match $repoRoot -and
    $_.CommandLine -match 'uvicorn(?:\.exe)?["'']?\s+backend\.app:app' -and
    $_.CommandLine -match '--port\s+(?:8000|8080)(?:\s|$)'
})
$processIds = @($processes | ForEach-Object { $_.ProcessId })
$roots = @($processes | Where-Object { $processIds -notcontains $_.ParentProcessId })

if ($roots.Count -eq 0) {
    Write-Output 'No pan server processes found.'
    exit 0
}

foreach ($process in $roots) {
    & taskkill.exe /PID $process.ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to stop pan server process tree (PID $($process.ProcessId))."
        exit $LASTEXITCODE
    }
    Write-Output "Stopped pan server process tree (PID $($process.ProcessId))."
}
