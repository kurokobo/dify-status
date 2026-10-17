<#
.SYNOPSIS
    Replays the pending trigger_id on newly rotated webhook apps and outputs gh secret set commands.

.DESCRIPTION
    This is step 2 of the webhook app rotation process. Run webhook-apps-step1-create.ps1 first,
    then copy-paste the $env: commands it outputs into this session before running this script.
    This script does not call Connect-Dify; it only replays webhook triggers with the exported tokens.

    The script will:
      1. Pull the latest state from origin/main.
      2. Read the pending trigger_id from the state file.
      3. Replay the pending trigger_id on the new app using the credentials from $env:.
      4. Poll until the workflow succeeds (or times out).
      5. Print gh secret set commands for copy-pasting.

    Prerequisites:
      - $env: variables set from webhook-apps-step1-create.ps1 output (in the current session)
      - git and gh CLIs available

    Usage:
      cd <repo-root>
      # paste $env: commands from webhook-apps-step1-create.ps1 output, then:
      .\helper\webhook-apps-step2-cutover.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TRIGGER_BASE  = "https://trigger.ai-plugin.io/triggers/webhook"
$DIFY_API_BASE = "https://api.dify.ai/v1"
$STATE_DIR     = Join-Path $PSScriptRoot "..\data\.webhook_state"

# ─────────────────────────────────────────────────────────────
# Validate required env vars
# ─────────────────────────────────────────────────────────────
if (-not $env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN -or -not $env:DIFY_WEBHOOK_PRO_API_KEY) {
    Write-Host "ERROR: `$env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN / _API_KEY are not set." -ForegroundColor Red
    Write-Host "Run webhook-apps-step1-create.ps1 first and copy-paste the `$env: commands it outputs." -ForegroundColor Red
    exit 1
}

# ─────────────────────────────────────────────────────────────
# Replay trigger and poll for succeeded
# ─────────────────────────────────────────────────────────────
function Invoke-WebhookReplay {
    param(
        [string]$Label,
        [string]$TriggerId,
        [string]$TriggerToken,
        [string]$ApiKey
    )

    $Timestamp = [int](Get-Date -UFormat %s)
    $Body      = [pscustomobject]@{ id = $TriggerId; timestamp = $Timestamp } | ConvertTo-Json -Compress

    Write-Host ""
    Write-Host "[$Label] Replaying trigger_id: $TriggerId" -ForegroundColor Cyan

    try {
        Invoke-RestMethod -Method Post `
            -Uri "$TRIGGER_BASE/$TriggerToken" `
            -ContentType "application/json" `
            -Body $Body | Out-Null
    }
    catch {
        Write-Host "[$Label] Failed to send trigger: $_" -ForegroundColor Red
        return "trigger-failed"
    }

    Write-Host "[$Label] Trigger sent. Waiting for workflow to complete..." -ForegroundColor Cyan

    $MaxRetry = 12      # up to 12 attempts
    $Interval = 10      # 10-second interval => up to 2 minutes
    $LogUri   = "$DIFY_API_BASE/workflows/logs?keyword=$TriggerId&limit=1"
    $Headers  = @{ Authorization = "Bearer $ApiKey" }
    $Status   = "timeout"

    for ($i = 0; $i -lt $MaxRetry; $i++) {
        Start-Sleep -Seconds $Interval

        try {
            $Resp = Invoke-RestMethod -Uri $LogUri -Headers $Headers
            $Run  = $Resp.data |
                    Select-Object -First 1 |
                    Select-Object -ExpandProperty workflow_run -ErrorAction SilentlyContinue
        }
        catch {
            Write-Host "[$Label] Failed to fetch logs: $_" -ForegroundColor Yellow
            continue
        }

        if ($Run -and $Run.status) {
            $Status = $Run.status
            $Color  = switch ($Status) {
                "succeeded" { "Green" }
                "failed"    { "Red"   }
                "stopped"   { "Red"   }
                default     { "Yellow" }
            }
            Write-Host "[$Label] Status: $Status ($($i + 1)/$MaxRetry)" -ForegroundColor $Color
            if ($Status -in @("succeeded", "failed", "stopped")) { break }
        }
        else {
            Write-Host "[$Label] No log entry yet... ($($i + 1)/$MaxRetry)" -ForegroundColor Yellow
        }
    }

    return $Status
}

# ─────────────────────────────────────────────────────────────
# Pull latest state
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Pulling latest state from origin/main..." -ForegroundColor Cyan
git pull origin main

# ─────────────────────────────────────────────────────────────
# Webhook Pro
# ─────────────────────────────────────────────────────────────
$ProStatus = $null
Write-Host ""
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host " Webhook Pro" -ForegroundColor White
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray

$stateFile = Join-Path $STATE_DIR "webhook_pro.json"
if (-not (Test-Path $stateFile)) {
    Write-Host "[Webhook Pro] State file not found: $stateFile" -ForegroundColor Red
    $ProStatus = "no-state"
}
else {
    $state = Get-Content $stateFile | ConvertFrom-Json
    Write-Host "[Webhook Pro] trigger_id: $($state.trigger_id)" -ForegroundColor Cyan

    $ProStatus = Invoke-WebhookReplay `
        -Label        "Webhook Pro" `
        -TriggerId    $state.trigger_id `
        -TriggerToken $env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN `
        -ApiKey       $env:DIFY_WEBHOOK_PRO_API_KEY
}

# ─────────────────────────────────────────────────────────────
# Result summary
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " Result summary" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

$AllOk = $true

$color = if ($ProStatus -eq "succeeded") { "Green" } else { "Red" }
if ($ProStatus -ne "succeeded") { $AllOk = $false }
Write-Host "Webhook Pro: $ProStatus" -ForegroundColor $color

if (-not $AllOk) {
    Write-Host ""
    Write-Host "WARNING: the replay did not succeed." -ForegroundColor Red
    Write-Host "Verify the apps in the Dify Web UI before updating GitHub Secrets." -ForegroundColor Red
}

# ─────────────────────────────────────────────────────────────
# gh secret set commands
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "--------------------------------------------" -ForegroundColor Cyan
Write-Host " GitHub Secrets  (gh secret set)" -ForegroundColor Cyan
Write-Host "--------------------------------------------" -ForegroundColor Cyan
Write-Host "Run the following from the repository root:" -ForegroundColor DarkGray

Write-Host ""
Write-Host "gh secret set DIFY_WEBHOOK_PRO_TRIGGER_TOKEN --body `"$env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN`""
Write-Host "gh secret set DIFY_WEBHOOK_PRO_API_KEY        --body `"$env:DIFY_WEBHOOK_PRO_API_KEY`""
