param(
    [Parameter(Mandatory = $true)][string]$Email,
    [ValidateSet('codex', 'antigravity')][string]$Provider = 'codex',
    [string]$PythonExe = '',
    [string]$ManagerPath = '',
    [Parameter(Mandatory = $true)][string]$StatusPath,
    [switch]$Confirm
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ManagerBridge.ps1')
$utf16 = New-Object System.Text.UnicodeEncoding($false, $true)
$utf8 = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$mutex = [Threading.Mutex]::new($false, 'Local\VietSuiteQuotaSessionSwitch')
$locked = $false

function Write-SwitchStatus([string]$Message, [string]$Color) {
    $safe = $Message.Replace("`r", ' ').Replace("`n", ' ').Replace('#', '＃')
    $content = "[Variables]`r`nSwitchMessage=$safe`r`nSwitchColor=$Color`r`n"
    [IO.File]::WriteAllText($StatusPath, $content, $utf16)
}

try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) {
        Write-SwitchStatus 'Đang chuyển phiên, vui lòng chờ…' '251,191,36,255'
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($Email)) { throw 'Chưa xác định tài khoản cần chuyển.' }
    $null = Resolve-SessionManager

    if ($Confirm) {
        Add-Type -AssemblyName PresentationFramework
        $appName = if ($Provider -eq 'antigravity') { 'Antigravity' } else { 'Codex' }
        $prompt = "Chuyển $appName sang phiên $Email ?`n`nPhiên $appName hiện tại sẽ được backup trước khi restore."
        $choice = [Windows.MessageBox]::Show(
            $prompt,
            "Xác nhận chuyển phiên $appName",
            [Windows.MessageBoxButton]::YesNo,
            [Windows.MessageBoxImage]::Question,
            [Windows.MessageBoxResult]::No
        )
        if ($choice -ne [Windows.MessageBoxResult]::Yes) {
            Write-SwitchStatus 'Đã hủy chuyển phiên' '116,128,150,255'
            exit 0
        }
    }

    Write-SwitchStatus ('Đang chuyển sang ' + $Email + '…') '251,191,36,255'
    $result = Invoke-SessionManager -Arguments @('--provider', $Provider, '--switch-email', $Email) -Elevate:($Provider -eq 'antigravity')
    Write-SwitchStatus ([string]$result.message) '7,95,69,255'
    exit 0
} catch {
    Write-SwitchStatus ([string]$_.Exception.Message) '248,113,113,255'
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
