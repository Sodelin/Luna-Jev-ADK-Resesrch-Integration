param(
  [ValidatePattern('^[a-z][a-z0-9_-]{0,47}$')][string]$JobId = 'adk-repair',
  [ValidateRange(1,3)][int]$MaxSteps = 3,
  [ValidateSet('adk','plain')][string]$Engine = 'adk',
  [switch]$Wait
)
$ErrorActionPreference = 'Stop'
$loopPython = Join-Path $PSScriptRoot '.venv-adk\Scripts\python.exe'
$loopComponentsFile = Join-Path $PSScriptRoot 'components-runtime.local.json'
if (Test-Path -LiteralPath $loopComponentsFile) {
  $loopComponents = Get-Content -LiteralPath $loopComponentsFile -Raw | ConvertFrom-Json
  if ($loopComponents.adk_python) { $loopPython = $loopComponents.adk_python }
}
if (-not (Test-Path -LiteralPath $loopPython)) { throw 'Install requirements-adk.txt in .venv-adk first.' }
$loopLogs = Join-Path $PSScriptRoot 'loop-runs\launcher'
New-Item -ItemType Directory -Path $loopLogs -Force | Out-Null
$loopStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$loopStdout = Join-Path $loopLogs "$JobId-$loopStamp.stdout.txt"
$loopStderr = Join-Path $loopLogs "$JobId-$loopStamp.stderr.txt"
$loopArgs = if ($Engine -eq 'adk') { @('adk_loop.py', $JobId, '--max-steps', "$MaxSteps") }
            else { @('luna_loop.py', 'run', $JobId, '--max-steps', "$MaxSteps") }
$loopProcess = Start-Process -FilePath $loopPython -ArgumentList $loopArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $loopStdout -RedirectStandardError $loopStderr -PassThru
$loopReceipt = @{ job = $JobId; engine = $Engine; pid = $loopProcess.Id; stdout = $loopStdout; stderr = $loopStderr; max_steps = $MaxSteps }
if ($Wait) { $loopProcess.WaitForExit(); $loopReceipt.exit_code = $loopProcess.ExitCode }
$loopReceipt | ConvertTo-Json
