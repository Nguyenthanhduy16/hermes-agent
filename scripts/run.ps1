[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet('Setup', 'SetupSheets', 'SheetsCheck', 'Update', 'PrepareDocs', 'PrepareSheets', 'FeatureDemo', 'Report', 'Check', 'Demo', 'Test', 'TestHermes', 'ZaloCheck', 'Discover', 'Model', 'Start')]
    [string]$Action = 'Check'
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:UV_CACHE_DIR = Join-Path $taskRoot '.runtime\uv-cache'
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    if ($Action -in @('Setup', 'SetupSheets')) {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            $taskTools = Join-Path $taskRoot '.runtime\hermes-home\tools'
            $taskUv = Get-ChildItem -LiteralPath $taskTools -Directory -Filter 'uv-*' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending |
                ForEach-Object { Join-Path $_.FullName 'uv.exe' } |
                Where-Object { Test-Path -LiteralPath $_ } |
                Select-Object -First 1
            if (-not $taskUv) { throw 'uv not found. Install uv or complete Hermes installation first.' }
        } else { $taskUv = (Get-Command uv).Source }
        if (-not (Test-Path -LiteralPath $taskPython)) {
            & $taskUv venv --python 3.14 .venv
            if ($LASTEXITCODE -ne 0) { throw 'Cannot create project Python environment.' }
        }
        $taskPackage = if ($Action -eq 'SetupSheets') { '.[sheets]' } else { '.' }
        & $taskUv pip install --python $taskPython -e $taskPackage
        if ($LASTEXITCODE -ne 0) { throw 'Cannot install project dependencies.' }
        if ($Action -eq 'SetupSheets') {
            $taskHermes = Join-Path $taskRoot '.runtime\hermes-home\bin\hermes.exe'
            if (-not (Test-Path -LiteralPath $taskHermes)) { throw 'Complete Hermes installation first.' }
            $taskPriorHome = $env:HERMES_HOME
            $env:HERMES_HOME = Join-Path $taskRoot '.runtime\hermes-home'
            # Use Hermes' own locked dependency manager; its runtime is not a
            # conventional venv and must not be modified with raw pip writes.
            try { & $taskHermes --run-module pm.cli install --extra google }
            finally { $env:HERMES_HOME = $taskPriorHome }
            if ($LASTEXITCODE -ne 0) { throw 'Cannot install Google authentication support into Hermes.' }
        }
        & $taskPython scripts\configure.py
    } else {
        if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\scripts\run.ps1 Setup first.' }
        switch ($Action) {
            'Update' { & $taskPython scripts\configure.py }
            'PrepareDocs' { & $taskPython scripts\features.py prepare-docs }
            'PrepareSheets' { & $taskPython scripts\features.py prepare-sheets }
            'SheetsCheck' { & $taskPython scripts\features.py sheets-check }
            'FeatureDemo' { & $taskPython scripts\features.py demo }
            'Report' { & $taskPython scripts\features.py report }
            'Check' { & $taskPython -m hermes_zalo.cli check }
            'Demo' { & $taskPython -m hermes_zalo.cli demo }
            'Test' { & $taskPython -m unittest discover -s tests -v }
            'TestHermes' {
                # Native launchers preserve Unicode paths under legacy console
                # code pages. The UTF-8 .cmd fallback can misread accented paths.
                $taskHermes = @(
                    (Join-Path $taskRoot '.runtime\hermes-home\bin\hermes.exe'),
                    (Join-Path $taskRoot '.runtime\hermes-agent\.hermes\bin\hermes.exe')
                ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
                if (-not $taskHermes) { throw 'Native Hermes launcher (hermes.exe) is missing. Run .\scripts\install-hermes.ps1 to complete or repair installation.' }
                $taskPriorHermesHome = $env:HERMES_HOME
                $taskPriorIntegrationFlag = $env:HERMES_INTEGRATION_TESTS
                $env:HERMES_HOME = Join-Path $taskRoot '.runtime\hermes-home'
                $env:HERMES_INTEGRATION_TESTS = '1'
                try { & $taskHermes --run-module unittest discover -s tests -p test_hermes_integration.py -v }
                finally {
                    $env:HERMES_HOME = $taskPriorHermesHome
                    $env:HERMES_INTEGRATION_TESTS = $taskPriorIntegrationFlag
                }
            }
            'ZaloCheck' { & $taskPython -m hermes_zalo.cli probe }
            'Discover' { & $taskPython -m hermes_zalo.cli discover }
            'Model' { & $taskPython scripts\launch.py model }
            'Start' { & $taskPython scripts\launch.py gateway run }
        }
    }
    $taskExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $taskExitCode
