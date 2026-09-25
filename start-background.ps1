param([switch]$Wait)
$ErrorActionPreference='Stop'
$scriptRoot=$PSScriptRoot
$python=(Get-Command python -ErrorAction Stop).Source
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$argsList=@(('"'+(Join-Path $scriptRoot 'runner.py')+'"'),'run')
$proc=Start-Process -FilePath $python -ArgumentList $argsList -WindowStyle Hidden -WorkingDirectory $scriptRoot -RedirectStandardOutput (Join-Path $scriptRoot ($stamp+'.stdout.log')) -RedirectStandardError (Join-Path $scriptRoot ($stamp+'.stderr.log')) -PassThru
Write-Output ('Finite verification queue started. PID: '+$proc.Id)
if ($Wait) { $proc.WaitForExit(); Write-Output ('Exit code: '+$proc.ExitCode) }
