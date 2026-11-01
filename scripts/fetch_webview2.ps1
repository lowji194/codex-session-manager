# Download the pinned Microsoft Fixed Version Runtime. Does not launch the app/runtime.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$runtimeVersion = '156.0.4314.58'
$runtimeArchitecture = 'x64'
$expectedCabSha256 = '857DCFD6F7035508A29645FA2978C3A6A841F220DF77CC366D318894E19F4792'
$downloadUrl = 'https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/b7919cc9-680d-4253-bf32-732988e68aa6/Microsoft.WebView2.FixedVersionRuntime.156.0.4314.58.x64.cab'
$eulaUrl = 'https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us&fixed=true'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dependencyRoot = Join-Path $projectRoot '.build/dependencies/WebView2Runtime'
$versionFolder = "Microsoft.WebView2.FixedVersionRuntime.$runtimeVersion.$runtimeArchitecture"
$runtimePath = Join-Path $dependencyRoot $versionFolder
$runtimeExe = Join-Path $runtimePath 'msedgewebview2.exe'
$cabPath = Join-Path $dependencyRoot "$versionFolder.cab"
$eulaPath = Join-Path $dependencyRoot 'EULA-WebView2-FixedVersion.html'

if ($env:OS -ne 'Windows_NT') { throw 'WebView2 extraction and signature validation require Windows.' }
[System.IO.Directory]::CreateDirectory($dependencyRoot) | Out-Null
if (-not (Test-Path -LiteralPath $cabPath -PathType Leaf)) {
    Write-Verbose "Downloading Microsoft WebView2 $runtimeVersion ($runtimeArchitecture)."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $cabPath -UseBasicParsing
}
$actualCabSha256 = (Get-FileHash -LiteralPath $cabPath -Algorithm SHA256).Hash
if ($actualCabSha256 -ne $expectedCabSha256) {
    throw "WebView2 CAB checksum mismatch. Do not use this download. Expected $expectedCabSha256; received $actualCabSha256."
}

if (-not (Test-Path -LiteralPath $runtimeExe -PathType Leaf)) {
    $nativeExpand = Join-Path $env:SystemRoot 'System32/expand.exe'
    if (-not (Test-Path -LiteralPath $nativeExpand -PathType Leaf)) { throw 'Windows expand.exe was not found.' }
    $expandOutput = & $nativeExpand '-F:*' $cabPath $dependencyRoot 2>&1
    if ($LASTEXITCODE -ne 0) { throw "WebView2 CAB extraction failed: $($expandOutput | Select-Object -Last 8)" }
}
if (-not (Test-Path -LiteralPath $runtimeExe -PathType Leaf)) {
    throw "The extracted CAB did not contain the expected runtime: $runtimeExe"
}
$runtimeSignature = Get-AuthenticodeSignature -LiteralPath $runtimeExe
if ($runtimeSignature.Status -ne 'Valid' -or $runtimeSignature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
    throw 'WebView2 executable must have a valid Microsoft Corporation Authenticode signature.'
}
$actualVersion = (Get-Item -LiteralPath $runtimeExe).VersionInfo.FileVersion
if (-not $actualVersion.StartsWith($runtimeVersion)) {
    throw "Unexpected WebView2 executable version: $actualVersion"
}

# Check the PE architecture without executing any downloaded binary.
$exeStream = [System.IO.File]::OpenRead($runtimeExe)
$exeReader = [System.IO.BinaryReader]::new($exeStream)
try {
    if ($exeReader.ReadUInt16() -ne 0x5A4D) { throw 'WebView2 executable is not a PE file.' }
    $exeStream.Position = 0x3C
    $peOffset = $exeReader.ReadInt32()
    if ($peOffset -lt 0 -or $peOffset -gt ($exeStream.Length - 6)) { throw 'WebView2 PE header is invalid.' }
    $exeStream.Position = $peOffset
    if ($exeReader.ReadUInt32() -ne 0x00004550 -or $exeReader.ReadUInt16() -ne 0x8664) {
        throw 'The pinned runtime must be an x64 PE executable.'
    }
} finally {
    $exeReader.Dispose()
    $exeStream.Dispose()
}

# Preserve Microsoft's terms alongside the redistributable runtime.
$licenseReply = Invoke-RestMethod -Uri $eulaUrl -Method Get
if (-not $licenseReply.fixedHtml -or $licenseReply.fixedHtml -notmatch 'MICROSOFT EDGE WEBVIEW2 RUNTIME') {
    throw 'The Microsoft API did not return the expected Fixed Version Runtime license.'
}
$licenseDocument = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Microsoft Edge WebView2 Fixed Version Runtime license</title></head><body>' + $licenseReply.fixedHtml + '</body></html>'
[System.IO.File]::WriteAllText($eulaPath, $licenseDocument, [System.Text.UTF8Encoding]::new($false))
[ordered]@{
    version = $runtimeVersion
    architecture = $runtimeArchitecture
    download_url = $downloadUrl
    cab_sha256 = $actualCabSha256
    executable_sha256 = (Get-FileHash -LiteralPath $runtimeExe -Algorithm SHA256).Hash
    signature_status = $runtimeSignature.Status.ToString()
    signer = $runtimeSignature.SignerCertificate.Subject
    license_url = $eulaUrl
    license_sha256 = (Get-FileHash -LiteralPath $eulaPath -Algorithm SHA256).Hash
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dependencyRoot 'WebView2-runtime-source.json') -Encoding utf8

# The only success output is the absolute runtime folder, suitable for --webview2-runtime.
Write-Output ([System.IO.Path]::GetFullPath($runtimePath))
