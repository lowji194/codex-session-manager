$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$dialog=New-Object Windows.Forms.OpenFileDialog
$dialog.Title='Chọn CodexSessionManager.exe hoặc main.py'
$dialog.Filter='Session Manager|CodexSessionManager.exe;main.py'
if($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK){
    $path=$dialog.FileName
    if($path.Contains('#')){throw 'Đường dẫn chứa ký tự # không được Rainmeter hỗ trợ.'}
    $text="[Variables]`r`nSessionManagerPath=$path`r`nQuotaPythonExe=`r`nQuotaMaxAccounts=8`r`n"
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'Settings.inc'),$text,(New-Object Text.UnicodeEncoding($false,$true)))
    $rainmeter=Join-Path $env:ProgramFiles 'Rainmeter\Rainmeter.exe'
    if(Test-Path -LiteralPath $rainmeter){& $rainmeter '!Refresh' 'QuotaAI'}
}
$dialog.Dispose()
