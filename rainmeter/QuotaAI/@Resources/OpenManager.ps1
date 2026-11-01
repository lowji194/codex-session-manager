$ErrorActionPreference='Stop'
$ManagerPath=''
$PythonExe=''
Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Settings.inc') -Encoding Unicode | ForEach-Object {
    if($_ -match '^SessionManagerPath=(.*)$'){$ManagerPath=$Matches[1]}
    if($_ -match '^QuotaPythonExe=(.*)$'){$PythonExe=$Matches[1]}
}
. (Join-Path $PSScriptRoot 'ManagerBridge.ps1')
try{
    $manager=Resolve-SessionManager
    if([IO.Path]::GetExtension($manager) -eq '.py'){
        $python=Resolve-SessionPython $manager
        Start-Process -FilePath $python -ArgumentList (Quote-SessionArgument $manager) -WindowStyle Hidden
    }else{Start-Process -FilePath $manager}
}catch{
    Add-Type -AssemblyName PresentationFramework
    [Windows.MessageBox]::Show($_.Exception.Message,'Quota AI') | Out-Null
}
