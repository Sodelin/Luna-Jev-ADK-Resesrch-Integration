param(
  [Parameter(Mandatory=$true)]
  [ValidatePattern('^[a-z][a-z0-9_-]{0,19}$')][string]$QueueId,
  [switch]$Wait
)
$ErrorActionPreference = 'Stop'
$queuePython = Join-Path $PSScriptRoot '.venv-adk\Scripts\python.exe'
$queueRuntimeFile = Join-Path $PSScriptRoot 'components-runtime.local.json'
if (Test-Path -LiteralPath $queueRuntimeFile) {
  $queueRuntime = Get-Content -LiteralPath $queueRuntimeFile -Raw | ConvertFrom-Json
  if ($queueRuntime.adk_python) { $queuePython = $queueRuntime.adk_python }
}
if (-not (Test-Path -LiteralPath $queuePython -PathType Leaf)) {
  throw 'Configure the verified local Python runtime before launching a queue.'
}
$queueLogs = Join-Path $PSScriptRoot 'loop-runs\launcher'
New-Item -ItemType Directory -Path $queueLogs -Force | Out-Null
$queueStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$queueStdout = Join-Path $queueLogs "queue-$QueueId-$queueStamp.stdout.txt"
$queueStderr = Join-Path $queueLogs "queue-$QueueId-$queueStamp.stderr.txt"
$queueProcess = Start-Process -FilePath $queuePython -ArgumentList @('corollary_queue.py', 'dispatch', $QueueId) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $queueStdout -RedirectStandardError $queueStderr -PassThru
$queueLaunch = @{ queue = $QueueId; pid = $queueProcess.Id; stdout = $queueStdout; stderr = $queueStderr; execution_authorization_created = $false }
if ($Wait) { $queueProcess.WaitForExit(); $queueLaunch.exit_code = $queueProcess.ExitCode }
$queueLaunch | ConvertTo-Json
