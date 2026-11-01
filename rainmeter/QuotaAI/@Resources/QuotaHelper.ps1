param(
    [string]$PythonExe = '',
    [string]$ManagerPath = '',
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$CachePath,
    [ValidateRange(1, 20)][int]$MaxAccounts = 8,
    [switch]$CacheOnly
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$utf16 = New-Object System.Text.UnicodeEncoding($false, $true)
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
. (Join-Path $PSScriptRoot 'ManagerBridge.ps1')

function Get-QuotaEntries($Usage) {
    if ($null -eq $Usage -or $null -eq $Usage.quotas) { return @() }
    $entries = foreach ($property in $Usage.quotas.PSObject.Properties) {
        $label = if ($property.Name -match '\(([^)]+)\)') {
            $Matches[1].ToUpperInvariant()
        } elseif ($property.Name -match 'weekly') {
            '7D'
        } else {
            $property.Name.ToUpperInvariant()
        }
        [pscustomobject]@{ Label = $label; Quota = $property.Value }
    }
    return @($entries)
}

function Get-ResetDisplay($ResetAt) {
    $empty = [pscustomobject]@{ Time = '--:--'; Date = '--/--'; Relative = 'chưa rõ'; RelativeColor = '116,128,150,255' }
    if ($null -eq $ResetAt -or [string]::IsNullOrWhiteSpace([string]$ResetAt)) { return $empty }
    try {
        if ($ResetAt -is [DateTimeOffset]) {
            $local = $ResetAt.ToLocalTime()
        } elseif ($ResetAt -is [DateTime]) {
            $local = ([DateTimeOffset]$ResetAt).ToLocalTime()
        } else {
            $local = ([DateTimeOffset]::Parse([string]$ResetAt, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)).ToLocalTime()
        }
        $span = $local - [DateTimeOffset]::Now
        if ($span.TotalSeconds -le 0) {
            $relative = 'đang reset'
            $relativeColor = '248,113,113,255'
        } elseif ($span.TotalDays -ge 1) {
            $relative = 'còn {0}n {1}g' -f [math]::Floor($span.TotalDays), $span.Hours
            $relativeColor = '52,211,153,255'
        } elseif ($span.TotalHours -ge 1) {
            $relative = 'còn {0}g {1}p' -f [math]::Floor($span.TotalHours), $span.Minutes
            $relativeColor = '52,211,153,255'
        } else {
            $relative = 'còn {0}p' -f [math]::Max(1, [math]::Ceiling($span.TotalMinutes))
            $relativeColor = '251,191,36,255'
        }
        return [pscustomobject]@{ Time = $local.ToString('HH:mm'); Date = $local.ToString('dd/MM'); Relative = $relative; RelativeColor = $relativeColor }
    } catch {
        return $empty
    }
}

function Set-ColorAlpha([string]$Color, [int]$Alpha) {
    $parts = $Color -split ','
    if ($parts.Count -lt 3) { return "116,128,150,$Alpha" }
    return ($parts[0..2] -join ',') + ",$Alpha"
}

function Escape-Ini([object]$Value) {
    if ($null -eq $Value) { return '' }
    return ([string]$Value).Replace("`r", ' ').Replace("`n", ' ').Replace('#', '＃')
}

function Get-CurrentCodexEmail {
    try { return [string](Invoke-SessionManager -Arguments @('--provider', 'codex', '--current-email')).email } catch { return '' }
}
function Get-CurrentAntigravityEmail {
    try { return [string](Invoke-SessionManager -Arguments @('--provider', 'antigravity', '--current-email')).email } catch { return '' }
}

function Convert-Connection($Connection, $Usage, [string]$UsageError) {
    $providerKey = ([string]$Connection.provider).ToLowerInvariant()
    $isAntigravity = $providerKey -eq 'antigravity'
    $quotaEntries = if ($UsageError) { @() } else { @(Get-QuotaEntries $Usage) }
    $percents = New-Object System.Collections.Generic.List[int]
    $quotaParts = New-Object System.Collections.Generic.List[string]
    $resetParts = New-Object System.Collections.Generic.List[string]
    $details = New-Object System.Collections.Generic.List[object]

    foreach ($entry in $quotaEntries) {
        $quota = $entry.Quota
        # An absent/invalid window is not a full quota window.
        if (-not $quota.unlimited -and ($null -eq $quota.total -or [double]$quota.total -le 0)) { continue }
        $used = if ($null -ne $quota.used) { [double]$quota.used } else { 0 }
        $total = if ($null -ne $quota.total) { [double]$quota.total } else { 0 }
        $remaining = if ($null -ne $quota.remaining) { [double]$quota.remaining } else { [math]::Max(0, $total - $used) }
        $unlimited = [bool]$quota.unlimited
        $entryPercent = if ($unlimited) { 0 } elseif ($total -gt 0) { [int][math]::Min(100, [math]::Max(0, [math]::Round(($used / $total) * 100))) } else { 0 }
        $entryColor = if ($entryPercent -ge 90) { '248,113,113,255' } elseif ($entryPercent -ge 70) { '251,191,36,255' } else { '52,211,153,255' }
        $reset = Get-ResetDisplay $quota.resetAt
        $percents.Add($entryPercent)
        if ($unlimited) { $quotaParts.Add($entry.Label + ' ∞') }
        elseif ($total -gt 0) { $quotaParts.Add(('{0} {1}% · còn {2:g}' -f $entry.Label, $entryPercent, $remaining)) }
        else { $quotaParts.Add($entry.Label + ' —') }
        $resetParts.Add(('{0} {1} {2} {3}' -f $entry.Label, $reset.Time, $reset.Date, $reset.Relative))
        $details.Add([pscustomobject]@{
            Label = $entry.Label
            Percent = $entryPercent
            Badge = $entry.Label + ' ' + $(if ($unlimited) { '∞' } else { "$entryPercent%" })
            Remaining = if ($unlimited) { 'không giới hạn' } else { 'còn ' + ('{0:g}' -f $remaining) }
            Color = $entryColor
            BadgeColor = Set-ColorAlpha $entryColor 42
            ResetTime = $reset.Time
            ResetDate = $reset.Date
            ResetRelative = $reset.Relative
            ResetRelativeColor = $reset.RelativeColor
        })
    }

    $percent = if ($percents.Count -gt 0) { ($percents | Measure-Object -Maximum).Maximum } else { 0 }
    $color = if ($percent -ge 90) { '248,113,113,255' } elseif ($percent -ge 70) { '251,191,36,255' } else { '52,211,153,255' }
    $quotaText = if ($UsageError) { 'Không đọc được quota' } elseif ($quotaParts.Count -gt 0) { $quotaParts -join '  |  ' } elseif ($isAntigravity) { 'Phiên đăng nhập AGY' } else { 'Chưa có dữ liệu quota' }
    $resetText = if ($UsageError) { $UsageError } elseif ($resetParts.Count -gt 0) { $resetParts -join '  |  ' } elseif ($isAntigravity) { 'Chưa có quota của phiên chưa kích hoạt' } else { 'Không có thời gian reset' }
    if (-not $Connection.isActive) {
        $status = 'TẮT'
        $statusColor = '116,128,150,255'
    } elseif ($Connection.testStatus -ne 'active') {
        $status = ([string]$Connection.testStatus).ToUpperInvariant()
        $statusColor = if ($Connection.testStatus -eq 'unavailable') { '251,191,36,255' } else { '248,113,113,255' }
    } elseif ($details.Count -eq 0) {
        $status = 'CHƯA CÓ QUOTA'
        $statusColor = '251,191,36,255'
    } elseif ($isAntigravity) {
        $status = 'SẴN SÀNG'
        $statusColor = '16,185,129,255'
    } elseif ($Usage.limitReached) {
        $status = 'ĐÃ HẾT QUOTA'
        $statusColor = '248,113,113,255'
    } else {
        $status = 'HOẠT ĐỘNG'
        $statusColor = '52,211,153,255'
    }
    $providerColor = if ($providerKey -eq 'claude') { '217,119,87,255' } elseif ($isAntigravity) { '16,185,129,255' } else { '56,189,248,255' }
    $emptyDetail = [pscustomobject]@{ Label=''; Percent=0; Badge=''; Remaining=''; Color='116,128,150,255'; BadgeColor='116,128,150,24'; ResetTime=''; ResetDate=''; ResetRelative=''; ResetRelativeColor='116,128,150,255' }
    # Two compact bars: the most constrained window of each AGY model group.
    # Keep every window in quotaText/resetText for the tooltip.
    $displayDetails = $details.ToArray()
    if ($isAntigravity) {
        $displayDetails = @()
        foreach ($prefix in @('GEM', '3P')) {
            $candidate = @($details | Where-Object { $_.Label -match ('^' + $prefix + '(?:\s|$)') } | Sort-Object -Property Percent -Descending | Select-Object -First 1)
            if ($candidate.Count -gt 0) { $displayDetails += $candidate[0] }
        }
        if ($displayDetails.Count -eq 0) { $displayDetails = @($details | Select-Object -First 2) }
    }
    $quota1 = if ($displayDetails.Count -ge 1) { $displayDetails[0] } else { $emptyDetail }
    $quota2 = if ($displayDetails.Count -ge 2) { $displayDetails[1] } else { $emptyDetail }
    $resetCreditsAvailable = $null
    if ($null -ne $Usage -and $null -ne $Usage.resetCredits) {
        $resetCreditsAvailable = [int]$Usage.resetCredits
    }
    $resetCreditsColor = if ($null -eq $resetCreditsAvailable) {
        '116,128,150,255'
    } elseif ($resetCreditsAvailable -gt 0) {
        '251,191,36,255'
    } else {
        '116,128,150,255'
    }

    [pscustomobject]@{
        id = [string]$Connection.id
        provider = $providerKey
        name = if ($Connection.name) { [string]$Connection.name } elseif ($Connection.email) { [string]$Connection.email } else { [string]$Connection.id }
        priority = [int]$Connection.priority
        active = [bool]$Connection.isActive
        plan = if ($Usage.plan) { ([string]$Usage.plan).ToUpperInvariant() } else { '' }
        status = $status
        statusColor = $statusColor
        providerColor = $providerColor
        percent = [int]$percent
        color = $color
        quotaText = $quotaText
        resetText = $resetText
        quota1Hidden = if ($displayDetails.Count -ge 1) { 0 } else { 1 }
        quota1Percent = [int]$quota1.Percent
        quota1X = if ($displayDetails.Count -ge 2) { 276 } else { 362 }
        quota1Badge = $quota1.Badge
        quota1Remaining = $quota1.Remaining
        quota1Color = $quota1.Color
        quota1BadgeColor = $quota1.BadgeColor
        reset1Label = $quota1.Label
        reset1Time = $quota1.ResetTime
        reset1Date = $quota1.ResetDate
        reset1Relative = $quota1.ResetRelative
        reset1RelativeColor = $quota1.ResetRelativeColor
        quota2Hidden = if ($displayDetails.Count -ge 2) { 0 } else { 1 }
        quota2Percent = [int]$quota2.Percent
        quota2X = 362
        quota2Badge = $quota2.Badge
        quota2Remaining = $quota2.Remaining
        quota2Color = $quota2.Color
        quota2BadgeColor = $quota2.BadgeColor
        reset2Label = $quota2.Label
        reset2Time = $quota2.ResetTime
        reset2Date = $quota2.ResetDate
        reset2Relative = $quota2.ResetRelative
        reset2RelativeColor = $quota2.ResetRelativeColor
        resetCreditsBadge = 'RESET ' + $(if ($null -eq $resetCreditsAvailable) { '—' } else { [string]$resetCreditsAvailable })
        resetCreditsColor = $resetCreditsColor
        resetCreditsBadgeColor = Set-ColorAlpha $resetCreditsColor $(if ($null -ne $resetCreditsAvailable -and $resetCreditsAvailable -gt 0) { 52 } else { 24 })
        quotaCount = $details.Count
    }
}

$accounts = @()
$sourceStatus = 'LOCAL'
$errorText = ''

try {
    if ($CacheOnly) {
        if (-not (Test-Path -LiteralPath $CachePath)) { throw 'Chưa có dữ liệu quota đã lưu.' }
        $cached = Get-Content -LiteralPath $CachePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $accounts = @($cached.accounts | Select-Object -First $MaxAccounts)
        $sourceStatus = 'CACHE'
    } else {
    $payload = Invoke-SessionManager -Arguments @('--rainmeter-quota-json', '--max-accounts', [string]$MaxAccounts)
    foreach ($entry in @($payload.accounts | Select-Object -First $MaxAccounts)) {
        $accounts += Convert-Connection $entry.connection $entry.usage ([string]$entry.error)
    }
    if ($payload.error) { $errorText = [string]$payload.error }

    $cacheObject = [pscustomobject]@{
        fetchedAt = if ($payload.fetchedAt) { [string]$payload.fetchedAt } else { [DateTimeOffset]::Now.ToString('o') }
        source = 'local-session'
        total = [int]$payload.total
        accounts = $accounts
    }
    [System.IO.File]::WriteAllText($CachePath, ($cacheObject | ConvertTo-Json -Depth 8), $utf8)
    }
} catch {
    $sourceStatus = 'CACHE'
    $errorText = $_.Exception.Message
    if (Test-Path -LiteralPath $CachePath) {
        $cached = Get-Content -LiteralPath $CachePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $accounts = @($cached.accounts)
    }
}

$visible = [math]::Min($MaxAccounts, $accounts.Count)
$currentCodexEmail = Get-CurrentCodexEmail
$currentAntigravityEmail = Get-CurrentAntigravityEmail
$panelHeight = if ($visible -gt 0) { 90 + (92 * $visible) } else { 156 }
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('[Variables]')
$lines.Add('AutoFetchOnLoad=0')
$lines.Add('QuotaCount=' + $visible)
$lines.Add('QuotaPanelH=' + $panelHeight)
$lines.Add('QuotaSourceStatus=' + $sourceStatus)
$lines.Add('QuotaLastSync=' + (Get-Date -Format 'HH:mm:ss dd/MM/yyyy'))
$lines.Add('QuotaError=' + (Escape-Ini $errorText))

for ($i = 1; $i -le $MaxAccounts; $i++) {
    if ($i -le $visible) {
        $account = $accounts[$i - 1]
        $providerKey = ([string]$account.provider).ToLowerInvariant()
        $currentProviderEmail = if ($providerKey -eq 'antigravity') { $currentAntigravityEmail } else { $currentCodexEmail }
        $isCurrent = (-not [string]::IsNullOrWhiteSpace($currentProviderEmail) -and
            ([string]$account.name).Trim().Equals($currentProviderEmail, [StringComparison]::OrdinalIgnoreCase))
        $lines.Add("Account${i}Hidden=0")
        $lines.Add("Account${i}Y=" + (84 + (($i - 1) * 92)))
        $lines.Add("Account${i}Name=" + (Escape-Ini $account.name))
        $lines.Add("Account${i}Provider=" + $(if ($providerKey -eq 'antigravity') { 'AGY' } else { Escape-Ini ([string]$account.provider).ToUpperInvariant() }))
        $lines.Add("Account${i}ProviderKey=" + (Escape-Ini $providerKey))
        $lines.Add("Account${i}Plan=" + (Escape-Ini $account.plan))
        $lines.Add("Account${i}Status=" + (Escape-Ini $account.status))
        $lines.Add("Account${i}StatusDisplay=" + (Escape-Ini $(if ($isCurrent) { 'ĐANG DÙNG · ' + $account.status } else { $account.status })))
        $lines.Add("Account${i}StatusDisplayColor=" + $(if ($isCurrent) { '167,139,250,255' } else { [string]$account.statusColor }))
        $lines.Add("Account${i}StatusColor=" + (Escape-Ini $account.statusColor))
        $lines.Add("Account${i}ProviderColor=" + (Escape-Ini $account.providerColor))
        $lines.Add("Account${i}Quota=" + (Escape-Ini $account.quotaText))
        $lines.Add("Account${i}Reset=" + (Escape-Ini $account.resetText))
        $lines.Add("Account${i}Quota1Hidden=" + [int]$account.quota1Hidden)
        $lines.Add("Account${i}Quota1Percent=" + [int]$account.quota1Percent)
        $lines.Add("Account${i}Quota1X=" + [int]$account.quota1X)
        $lines.Add("Account${i}Quota1Badge=" + (Escape-Ini $account.quota1Badge))
        $lines.Add("Account${i}Quota1Remaining=" + (Escape-Ini $account.quota1Remaining))
        $lines.Add("Account${i}Quota1Color=" + (Escape-Ini $account.quota1Color))
        $lines.Add("Account${i}Quota1BadgeColor=" + (Escape-Ini $account.quota1BadgeColor))
        $lines.Add("Account${i}Reset1Label=" + (Escape-Ini $account.reset1Label))
        $lines.Add("Account${i}Reset1Time=" + (Escape-Ini $account.reset1Time))
        $lines.Add("Account${i}Reset1Date=" + (Escape-Ini $account.reset1Date))
        $lines.Add("Account${i}Reset1Relative=" + (Escape-Ini $account.reset1Relative))
        $lines.Add("Account${i}Reset1RelativeColor=" + (Escape-Ini $account.reset1RelativeColor))
        $lines.Add("Account${i}Quota2Hidden=" + [int]$account.quota2Hidden)
        $lines.Add("Account${i}Quota2Percent=" + [int]$account.quota2Percent)
        $lines.Add("Account${i}Quota2X=" + [int]$account.quota2X)
        $lines.Add("Account${i}Quota2Badge=" + (Escape-Ini $account.quota2Badge))
        $lines.Add("Account${i}Quota2Remaining=" + (Escape-Ini $account.quota2Remaining))
        $lines.Add("Account${i}Quota2Color=" + (Escape-Ini $account.quota2Color))
        $lines.Add("Account${i}Quota2BadgeColor=" + (Escape-Ini $account.quota2BadgeColor))
        $lines.Add("Account${i}Reset2Label=" + (Escape-Ini $account.reset2Label))
        $lines.Add("Account${i}Reset2Time=" + (Escape-Ini $account.reset2Time))
        $lines.Add("Account${i}Reset2Date=" + (Escape-Ini $account.reset2Date))
        $lines.Add("Account${i}Reset2Relative=" + (Escape-Ini $account.reset2Relative))
        $lines.Add("Account${i}Reset2RelativeColor=" + (Escape-Ini $account.reset2RelativeColor))
        $lines.Add("Account${i}ResetCreditsBadge=" + (Escape-Ini $account.resetCreditsBadge))
        $lines.Add("Account${i}ResetCreditsColor=" + (Escape-Ini $account.resetCreditsColor))
        $lines.Add("Account${i}ResetCreditsBadgeColor=" + (Escape-Ini $account.resetCreditsBadgeColor))
        $lines.Add("Account${i}ResetCreditsHidden=" + $(if ($providerKey -eq 'antigravity') { 1 } else { 0 }))
        $lines.Add("Account${i}BarHidden=" + $(if ($providerKey -eq 'antigravity' -and [int]$account.quotaCount -eq 0) { 1 } else { 0 }))
        $lines.Add("Account${i}Percent=" + [int]$account.percent)
        $lines.Add("Account${i}Color=" + (Escape-Ini $account.color))
        $lines.Add("Account${i}Logo=" + $(if ($providerKey -eq 'antigravity') { '#CURRENTPATH#@Resources\Images\antigravity.png' } elseif ($providerKey -eq 'claude') { '#CURRENTPATH#@Resources\Images\claude.png' } else { '#CURRENTPATH#@Resources\Images\openai.png' }))
        $lines.Add("Account${i}Current=" + $(if ($isCurrent) { 1 } else { 0 }))
        $lines.Add("Account${i}ActiveHidden=" + $(if ($isCurrent) { 0 } else { 1 }))
        $lines.Add("Account${i}CardColor=#PillColor#")
        $lines.Add("Account${i}CardStrokeColor=" + $(if ($isCurrent) { '167,139,250,255' } else { [string]$account.providerColor }))
        $lines.Add("Account${i}CardStrokeWidth=" + $(if ($isCurrent) { 3 } else { 1 }))
        $lines.Add("Account${i}SwitchHidden=" + $(if ($isCurrent) { 1 } else { 0 }))
    } else {
        $lines.Add("Account${i}Hidden=1")
        $lines.Add("Account${i}Y=0")
        $lines.Add("Account${i}Name=")
        $lines.Add("Account${i}Provider=")
        $lines.Add("Account${i}ProviderKey=codex")
        $lines.Add("Account${i}Plan=")
        $lines.Add("Account${i}Status=")
        $lines.Add("Account${i}StatusDisplay=")
        $lines.Add("Account${i}StatusDisplayColor=116,128,150,255")
        $lines.Add("Account${i}StatusColor=116,128,150,255")
        $lines.Add("Account${i}ProviderColor=116,128,150,255")
        $lines.Add("Account${i}Quota=")
        $lines.Add("Account${i}Reset=")
        $lines.Add("Account${i}Quota1Hidden=1")
        $lines.Add("Account${i}Quota1Percent=0")
        $lines.Add("Account${i}Quota1X=362")
        $lines.Add("Account${i}Quota1Badge=")
        $lines.Add("Account${i}Quota1Remaining=")
        $lines.Add("Account${i}Quota1Color=116,128,150,255")
        $lines.Add("Account${i}Quota1BadgeColor=116,128,150,24")
        $lines.Add("Account${i}Reset1Label=")
        $lines.Add("Account${i}Reset1Time=")
        $lines.Add("Account${i}Reset1Date=")
        $lines.Add("Account${i}Reset1Relative=")
        $lines.Add("Account${i}Reset1RelativeColor=116,128,150,255")
        $lines.Add("Account${i}Quota2Hidden=1")
        $lines.Add("Account${i}Quota2Percent=0")
        $lines.Add("Account${i}Quota2X=362")
        $lines.Add("Account${i}Quota2Badge=")
        $lines.Add("Account${i}Quota2Remaining=")
        $lines.Add("Account${i}Quota2Color=116,128,150,255")
        $lines.Add("Account${i}Quota2BadgeColor=116,128,150,24")
        $lines.Add("Account${i}Reset2Label=")
        $lines.Add("Account${i}Reset2Time=")
        $lines.Add("Account${i}Reset2Date=")
        $lines.Add("Account${i}Reset2Relative=")
        $lines.Add("Account${i}Reset2RelativeColor=116,128,150,255")
        $lines.Add("Account${i}ResetCreditsBadge=RESET —")
        $lines.Add("Account${i}ResetCreditsColor=116,128,150,255")
        $lines.Add("Account${i}ResetCreditsBadgeColor=116,128,150,24")
        $lines.Add("Account${i}ResetCreditsHidden=1")
        $lines.Add("Account${i}BarHidden=1")
        $lines.Add("Account${i}Percent=0")
        $lines.Add("Account${i}Color=107,114,128,255")
        $lines.Add("Account${i}Logo=#CURRENTPATH#@Resources\Images\openai.png")
        $lines.Add("Account${i}Current=0")
        $lines.Add("Account${i}ActiveHidden=1")
        $lines.Add("Account${i}CardColor=#PillColor#")
        $lines.Add("Account${i}CardStrokeColor=116,128,150,255")
        $lines.Add("Account${i}CardStrokeWidth=1")
        $lines.Add("Account${i}SwitchHidden=1")
    }
}

[System.IO.File]::WriteAllText($OutputPath, ($lines -join "`r`n") + "`r`n", $utf16)
