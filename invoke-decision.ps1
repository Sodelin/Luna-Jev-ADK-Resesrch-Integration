param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [ValidateSet('base','typed')][string]$Model='base'
)
$ErrorActionPreference='Stop'
$cfg=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-config.local.json') -Raw | ConvertFrom-Json
& $cfg.python (Join-Path $PSScriptRoot 'decision_cli.py') --model $Model --model-root $cfg.model_root --input $InputFile
exit $LASTEXITCODE
