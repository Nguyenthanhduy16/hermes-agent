[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskRuntime = Join-Path $taskRoot '.runtime'
$taskCommit = '93c9360a8da592cee43e8895403e91c7a920b0ce'
New-Item -ItemType Directory -Force -Path $taskRuntime | Out-Null
$taskInstaller = Join-Path $taskRuntime 'hermes-install.ps1'
$taskUrl = "https://raw.githubusercontent.com/NousResearch/hermes-agent/$taskCommit/scripts/install.ps1"
Invoke-WebRequest -Uri $taskUrl -OutFile $taskInstaller
$taskExpectedHash = '770dc7ca7872b0f0649b1c1949bf791d815c4c833ffc536350f1dda248d3bdf6'
if ((Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskExpectedHash) {
    throw 'Official installer hash differs from the reviewed version. Stop and review before updating the pin.'
}
$env:UV_CACHE_DIR = Join-Path $taskRuntime 'uv-cache'
& $taskInstaller -Commit $taskCommit -HermesHome (Join-Path $taskRuntime 'hermes-home') -InstallDir (Join-Path $taskRuntime 'hermes-agent') -NonInteractive -SkipBrowser -SkipComputerUse
if ($LASTEXITCODE -ne 0) { throw 'Hermes installation failed; see .runtime/hermes-home/logs/install.log.' }
