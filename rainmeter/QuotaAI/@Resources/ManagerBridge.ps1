# Shared portable/source invocation using temporary JSON reports.
function Resolve-SessionManager {
    $candidates = @($ManagerPath, $env:CODEX_SESSION_MANAGER_PATH, (Join-Path $PSScriptRoot '..\..\..\CodexSessionManager.exe'))
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $resolved = (Resolve-Path -LiteralPath $candidate).Path
            if ([IO.Path]::GetExtension($resolved) -in @('.exe','.py')) { return $resolved }
        }
    }
    throw 'Chọn đường dẫn Session Manager trong menu chuột phải của skin.'
}
function Resolve-SessionPython([string]$ScriptPath) {
    $candidates = @($PythonExe, (Join-Path (Split-Path $ScriptPath) '.venv\Scripts\python.exe'))
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    throw 'Bản mã nguồn cần Python. Hãy dùng EXE portable hoặc tạo môi trường .venv.'
}
function Quote-SessionArgument([string]$Value) {
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw 'Tham số Session Manager không hợp lệ.' }
    return '"' + $Value + '"'
}
function Invoke-SessionManager {
    param([string[]]$Arguments, [switch]$Elevate)
    $manager = Resolve-SessionManager
    $report = Join-Path ([IO.Path]::GetTempPath()) ('quota-ai-' + [Guid]::NewGuid().ToString('N') + '.json')
    try {
        $program = $manager
        $invokeArguments = @($Arguments) + @('--report', $report)
        if ([IO.Path]::GetExtension($manager) -eq '.py') { $program = Resolve-SessionPython $manager; $invokeArguments = @($manager) + $invokeArguments }
        $argumentLine = ($invokeArguments | ForEach-Object { Quote-SessionArgument ([string]$_) }) -join ' '
        $options = @{FilePath=$program; ArgumentList=$argumentLine; WindowStyle='Hidden'; Wait=$true; PassThru=$true}
        if ($Elevate) { $options.Verb = 'RunAs' }
        $process = Start-Process @options
        if (-not (Test-Path -LiteralPath $report -PathType Leaf)) { throw 'Session Manager chưa hỗ trợ JSON report; cần bản 1.2.0 trở lên.' }
        $result = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($process.ExitCode -ne 0 -or $result.ok -eq $false) { throw $(if ($result.message) { [string]$result.message } else { 'Session Manager không thực hiện được thao tác.' }) }
        return $result
    } finally {
        if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report -Force }
    }
}
