[CmdletBinding()]
param(
    [switch]$Upload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
$secretFile = Join-Path $repoRoot "garmin_tokens_b64.txt"

Push-Location $repoRoot
try {
    if ($Upload) {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
            throw "GitHub CLI (gh) is not installed. Run without -Upload and paste the printed value manually."
        }

        & gh auth status --hostname github.com
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub CLI is not authenticated. Run 'gh auth login -h github.com', then try again."
        }
    }

    & $python "login_once.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Garmin login failed (exit code $LASTEXITCODE)."
    }

    & $python "pack_token.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Token packing failed (exit code $LASTEXITCODE)."
    }

    $secret = Get-Content -LiteralPath $secretFile -Raw

    if ($Upload) {
        $secret | & gh secret set GARMIN_TOKENS_B64 --repo TorinChacko/garmin-dashboard
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub secret update failed (exit code $LASTEXITCODE)."
        }

        Write-Host ""
        Write-Host "Updated GitHub secret GARMIN_TOKENS_B64."
        Write-Host "Delete garmin_tokens_b64.txt after confirming the next workflow run succeeds."
    }
    else {
        Write-Host ""
        Write-Host "Copy everything between the markers into GARMIN_TOKENS_B64:"
        Write-Host "----- BEGIN GARMIN_TOKENS_B64 -----"
        Write-Output $secret
        Write-Host "----- END GARMIN_TOKENS_B64 -----"
        Write-Host ""
        Write-Host "GitHub: https://github.com/TorinChacko/garmin-dashboard/settings/secrets/actions"
        Write-Host "Delete garmin_tokens_b64.txt after confirming the next workflow run succeeds."
    }
}
finally {
    Pop-Location
}
