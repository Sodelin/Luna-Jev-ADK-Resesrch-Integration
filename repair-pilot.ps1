param(
  [ValidateSet('ADK','TypeSafe')][string]$Component,
  [string]$Python312 = 'python'
)
$ErrorActionPreference = 'Stop'
if (-not $Component) { throw 'Choose -Component ADK or -Component TypeSafe. This does not launch model or proof work.' }
$repairUv = (Get-Command uv -ErrorAction Stop).Source
$repairStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$repairEnvironment = if ($Component -eq 'ADK') { ".venv-adk-repair-$repairStamp" } else { ".venv-typesafe-repair-$repairStamp" }
$repairRequirements = if ($Component -eq 'ADK') { 'requirements-adk.lock.txt' } else { 'requirements-typesafe.lock.txt' }
$repairPython = Join-Path $PSScriptRoot "$repairEnvironment\Scripts\python.exe"
$repairCache = Join-Path $PSScriptRoot '.uv-cache-repair'
& $repairUv venv --cache-dir $repairCache --python $Python312 (Join-Path $PSScriptRoot $repairEnvironment)
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed. Existing data was preserved.' }
& $repairUv pip install --python $repairPython --cache-dir $repairCache --link-mode copy --requirement (Join-Path $PSScriptRoot $repairRequirements)
if ($LASTEXITCODE -ne 0) { throw 'Package repair failed. Do not reset proof or budget files.' }
& $repairUv pip check --python $repairPython --cache-dir $repairCache
if ($LASTEXITCODE -ne 0) { throw 'Dependency compatibility check failed.' }
if ($Component -eq 'ADK') {
  & $repairPython -c 'from google.adk import Workflow,Runner; print("ADK import passed")'
} else {
  & $repairPython (Join-Path $PSScriptRoot 'typesafe_readiness.py') --self-test
}
if ($LASTEXITCODE -ne 0) { throw 'Rebuilt environment failed its offline check; active path not changed.' }
$repairConfigPath = Join-Path $PSScriptRoot 'components-runtime.local.json'
$repairConfig = @{}
if (Test-Path -LiteralPath $repairConfigPath) {
  $repairPrior = Get-Content -LiteralPath $repairConfigPath -Raw | ConvertFrom-Json
  foreach ($repairProperty in $repairPrior.PSObject.Properties) { $repairConfig[$repairProperty.Name] = $repairProperty.Value }
}
$repairKey = if ($Component -eq 'ADK') { 'adk_python' } else { 'typesafe_python' }
$repairConfig[$repairKey] = $repairPython
$repairConfig | ConvertTo-Json | Set-Content -LiteralPath "$repairConfigPath.tmp" -Encoding utf8
Move-Item -LiteralPath "$repairConfigPath.tmp" -Destination $repairConfigPath -Force
Write-Output "Verified package repair now selected: $repairPython"
Write-Output 'Prior environments and all budget/proof files were preserved. No model or Lean check was launched.'
