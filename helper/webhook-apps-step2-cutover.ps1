<#
.SYNOPSIS
    Replays the pending trigger_id on newly rotated webhook apps and outputs gh secret set commands.

.DESCRIPTION
    This is step 2 of the webhook app rotation process. Run webhook-apps-step1-create.ps1 first,
    then copy-paste the $env: commands it outputs into this session before running this script.

    The script will:
      1. Pull the latest state from origin/main.
      2. Read the pending trigger_id (and account_index for Free) from the state files.
      3. Replay the pending trigger_id on the correct new app using the credentials from $env:.
      4. Poll until the workflow succeeds (or times out).
      5. Print gh secret set commands for copy-pasting.

    Prerequisites:
      - $env: variables set from webhook-apps-step1-create.ps1 output (in the current session)
      - git and gh CLIs available

    Usage:
      cd <repo-root>
      # paste $env: commands from webhook-apps-step1-create.ps1 output, then:
      .\helper\webhook-apps-step2-cutover.ps1 -Pro          # Webhook Pro only
      .\helper\webhook-apps-step2-cutover.ps1 -Free         # Webhook Free only
      .\helper\webhook-apps-step2-cutover.ps1 -Pro -Free    # both
#>
param(
    [switch]$Pro,
    [switch]$Free
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TRIGGER_BASE  = "https://trigger.ai-plugin.io/triggers/webhook"
$DIFY_API_BASE = "https://api.dify.ai/v1"
$STATE_DIR     = Join-Path $PSScriptRoot "..\data\.webhook_state"

# ─────────────────────────────────────────────────────────────
# Determine which accounts to process
# ─────────────────────────────────────────────────────────────
if ($Pro -or $Free) {
    # Explicit: use parameters
    $DoPro  = $Pro.IsPresent
    $DoFree = $Free.IsPresent
} else {
    # Implicit: detect from env vars (fallback when no params given)
    $DoPro  = $env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN  -and $env:DIFY_WEBHOOK_PRO_API_KEY
    $DoFree = $env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1 -and $env:DIFY_WEBHOOK_FREE_API_KEY_1 -and
              $env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2 -and $env:DIFY_WEBHOOK_FREE_API_KEY_2
}

# Validate required env vars are present
if ($DoPro -and (-not $env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN -or -not $env:DIFY_WEBHOOK_PRO_API_KEY)) {
    Write-Host "ERROR: -Pro specified but `$env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN / _API_KEY are not set." -ForegroundColor Red
    exit 1
}
if ($DoFree -and (-not $env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1 -or -not $env:DIFY_WEBHOOK_FREE_API_KEY_1 -or
                  -not $env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2 -or -not $env:DIFY_WEBHOOK_FREE_API_KEY_2)) {
    Write-Host "ERROR: -Free specified but one or more `$env:DIFY_WEBHOOK_FREE_* variables are not set." -ForegroundColor Red
    exit 1
}

if (-not $DoPro -and -not $DoFree) {
    Write-Host "ERROR: No accounts to process. Pass -Pro, -Free, or both; or set the `$env: variables." -ForegroundColor Red
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
if ($DoPro) {
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
}

# ─────────────────────────────────────────────────────────────
# Webhook Free
# ─────────────────────────────────────────────────────────────
$FreeStatus = $null
if ($DoFree) {
    Write-Host ""
    Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host " Webhook Free" -ForegroundColor White
    Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray

    $stateFile = Join-Path $STATE_DIR "webhook_free.json"
    if (-not (Test-Path $stateFile)) {
        Write-Host "[Webhook Free] State file not found: $stateFile" -ForegroundColor Red
        $FreeStatus = "no-state"
    }
    else {
        $state        = Get-Content $stateFile | ConvertFrom-Json
        $accountIndex = [int]$state.account_index

        Write-Host "[Webhook Free] trigger_id:    $($state.trigger_id)" -ForegroundColor Cyan
        Write-Host "[Webhook Free] account_index: $accountIndex" -ForegroundColor Cyan

        $tokens = @(
            @{ Token = $env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1; ApiKey = $env:DIFY_WEBHOOK_FREE_API_KEY_1 },
            @{ Token = $env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2; ApiKey = $env:DIFY_WEBHOOK_FREE_API_KEY_2 }
        )

        $FreeStatus = Invoke-WebhookReplay `
            -Label        "Webhook Free (account $accountIndex)" `
            -TriggerId    $state.trigger_id `
            -TriggerToken $tokens[$accountIndex].Token `
            -ApiKey       $tokens[$accountIndex].ApiKey
    }
}

# ─────────────────────────────────────────────────────────────
# Result summary
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " Result summary" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

$AllOk = $true

if ($DoPro) {
    $color = if ($ProStatus -eq "succeeded") { "Green" } else { "Red" }
    if ($ProStatus -ne "succeeded") { $AllOk = $false }
    Write-Host "Webhook Pro:  $ProStatus" -ForegroundColor $color
}
if ($DoFree) {
    $color = if ($FreeStatus -eq "succeeded") { "Green" } else { "Red" }
    if ($FreeStatus -ne "succeeded") { $AllOk = $false }
    Write-Host "Webhook Free: $FreeStatus" -ForegroundColor $color
}

if (-not $AllOk) {
    Write-Host ""
    Write-Host "WARNING: one or more replays did not succeed." -ForegroundColor Red
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

if ($DoPro) {
    Write-Host ""
    Write-Host "gh secret set DIFY_WEBHOOK_PRO_TRIGGER_TOKEN --body `"$env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN`""
    Write-Host "gh secret set DIFY_WEBHOOK_PRO_API_KEY        --body `"$env:DIFY_WEBHOOK_PRO_API_KEY`""
}
if ($DoFree) {
    Write-Host ""
    Write-Host "gh secret set DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1 --body `"$env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1`""
    Write-Host "gh secret set DIFY_WEBHOOK_FREE_API_KEY_1        --body `"$env:DIFY_WEBHOOK_FREE_API_KEY_1`""
    Write-Host "gh secret set DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2 --body `"$env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2`""
    Write-Host "gh secret set DIFY_WEBHOOK_FREE_API_KEY_2        --body `"$env:DIFY_WEBHOOK_FREE_API_KEY_2`""
}
