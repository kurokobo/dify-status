<#
.SYNOPSIS
    Automates Webhook app rotation using PSDify.

.DESCRIPTION
    For each of Webhook Pro, Free 1, and Free 2:
      1. Prompts for an email address (Enter to skip).
      2. Connects to Dify Cloud via email code authentication.
      3. Imports dsls/webhook.yml as a new app.
      4. Issues a new API key.
      5. Retrieves the webhook trigger token automatically via the console API.
      6. Publishes the app.
      7. Runs a test trigger and polls for a succeeded status.
    Prints a summary of all environment variables at the end.

    Prerequisites:
      - PSDify module installed:
          Install-Module PSDify   or   Import-Module .\PSDify.psd1
      - This script is located in helper/ under the repository root.

    Usage:
      cd <repo-root>
      .\helper\webhook-apps-step1-create.ps1                                          # interactive
      .\helper\webhook-apps-step1-create.ps1 -ProEmail you@example.com                # Pro only
      .\helper\webhook-apps-step1-create.ps1 -Free1Email a@x.com -Free2Email b@x.com  # Free only
#>
param(
    [string]$ProEmail   = $null,
    [string]$Free1Email = $null,
    [string]$Free2Email = $null
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DSL_PATH     = Join-Path $PSScriptRoot "..\dsls\webhook.yml"
$TRIGGER_BASE = "https://trigger.ai-plugin.io/triggers/webhook"
$DIFY_API_BASE = "https://api.dify.ai/v1"

# If any parameter was supplied, skip interactive prompts for unspecified accounts.
$ParamMode = $PSBoundParameters.ContainsKey('ProEmail') -or
             $PSBoundParameters.ContainsKey('Free1Email') -or
             $PSBoundParameters.ContainsKey('Free2Email')

# Collected results per account (Pro / Free1 / Free2)
$Results = [ordered]@{
    Pro   = @{ ApiKey = $null; TriggerToken = $null; AppName = $null }
    Free1 = @{ ApiKey = $null; TriggerToken = $null; AppName = $null }
    Free2 = @{ ApiKey = $null; TriggerToken = $null; AppName = $null }
}

# ─────────────────────────────────────────────────────────────
# Import app, issue API key, retrieve trigger token, publish
# ─────────────────────────────────────────────────────────────
function Register-WebhookApp {
    param(
        [string]$Label,
        [string]$Email
    )

    Write-Host ""
    Write-Host "[$Label] Connecting to Dify Cloud..." -ForegroundColor Cyan
    Connect-Dify -AuthMethod Code -Email $Email | Out-Null

    Write-Host "[$Label] Importing DSL: $DSL_PATH" -ForegroundColor Cyan
    $App = Import-DifyApp -Path $DSL_PATH
    if ($App -is [array]) { $App = $App[0] }
    Write-Host "[$Label] App imported: $($App.Name) (ID: $($App.Id))" -ForegroundColor Green

    Write-Host "[$Label] Issuing API key..." -ForegroundColor Cyan
    $ApiKeyObj = $App | New-DifyAppAPIKey
    Write-Host "[$Label] API key: $($ApiKeyObj.Token)" -ForegroundColor Green

    # Retrieve the webhook trigger token via the console API
    Write-Host "[$Label] Retrieving webhook trigger token..." -ForegroundColor Cyan
    $Auth        = Get-PSDifyConsoleAuth
    $Draft       = Invoke-DifyRestMethod `
        -Uri            "$env:PSDIFY_URL/console/api/apps/$($App.Id)/workflows/draft" `
        -SessionOrToken $Auth
    $NodeId      = ($Draft.graph.nodes | Where-Object { $_.data.type -eq "trigger-webhook" }).id
    $Trigger     = Invoke-DifyRestMethod `
        -Uri            "$env:PSDIFY_URL/console/api/apps/$($App.Id)/workflows/triggers/webhook?node_id=$NodeId" `
        -SessionOrToken $Auth
    $TriggerToken = $Trigger.webhook_url -replace ".*/", ""
    Write-Host "[$Label] Trigger token: $TriggerToken" -ForegroundColor Green

    # Publish the app so the webhook is live
    Write-Host "[$Label] Publishing app..." -ForegroundColor Cyan
    $null = Invoke-DifyRestMethod `
        -Uri            "$env:PSDIFY_URL/console/api/apps/$($App.Id)/workflows/publish" `
        -Method         POST `
        -Body           "{}" `
        -SessionOrToken $Auth
    Write-Host "[$Label] App published." -ForegroundColor Green

    Disconnect-Dify | Out-Null

    return [PSCustomObject]@{
        ApiKey       = $ApiKeyObj.Token
        TriggerToken = $TriggerToken
        AppName      = $App.Name
    }
}

# ─────────────────────────────────────────────────────────────
# Fire a test trigger and poll until succeeded (or timeout)
# ─────────────────────────────────────────────────────────────
function Test-WebhookApp {
    param(
        [string]$Label,
        [string]$TriggerToken,
        [string]$ApiKey
    )

    $TestId    = "status-check-$([System.Guid]::NewGuid().ToString('N').Substring(0, 12))"
    $Timestamp = [int](Get-Date -UFormat %s)
    $Body      = [pscustomobject]@{ id = $TestId; timestamp = $Timestamp } | ConvertTo-Json -Compress

    Write-Host ""
    Write-Host "[$Label] Firing test trigger: id = $TestId" -ForegroundColor Cyan

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
    $LogUri   = "$DIFY_API_BASE/workflows/logs?keyword=$TestId&limit=1"
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

            if ($Status -in @("succeeded", "failed", "stopped")) {
                break
            }
        }
        else {
            Write-Host "[$Label] No log entry yet... ($($i + 1)/$MaxRetry)" -ForegroundColor Yellow
        }
    }

    return $Status
}

# =====================================================
# Webhook Pro
# =====================================================
Write-Host ""
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host " Webhook Pro" -ForegroundColor White
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
if (-not $ParamMode) {
    Write-Host "Enter email address to rotate, or press Enter to skip." -ForegroundColor Yellow
    $ProEmail = Read-Host "Webhook Pro email"
}

if ($ProEmail) {
    $r = Register-WebhookApp -Label "Webhook Pro" -Email $ProEmail
    $Results.Pro.ApiKey       = $r.ApiKey
    $Results.Pro.TriggerToken = $r.TriggerToken
    $Results.Pro.AppName      = $r.AppName
} elseif ($ParamMode) {
    Write-Host "[Webhook Pro] No email specified — skipped." -ForegroundColor DarkGray
}

# =====================================================
# Webhook Free 1
# =====================================================
Write-Host ""
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host " Webhook Free 1" -ForegroundColor White
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
if (-not $ParamMode) {
    Write-Host "Enter email address to rotate, or press Enter to skip." -ForegroundColor Yellow
    $Free1Email = Read-Host "Webhook Free 1 email"
}

if ($Free1Email) {
    $r = Register-WebhookApp -Label "Webhook Free 1" -Email $Free1Email
    $Results.Free1.ApiKey       = $r.ApiKey
    $Results.Free1.TriggerToken = $r.TriggerToken
    $Results.Free1.AppName      = $r.AppName
} elseif ($ParamMode) {
    Write-Host "[Webhook Free 1] No email specified — skipped." -ForegroundColor DarkGray
}

# =====================================================
# Webhook Free 2
# =====================================================
Write-Host ""
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host " Webhook Free 2" -ForegroundColor White
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
if (-not $ParamMode) {
    Write-Host "Enter email address to rotate, or press Enter to skip." -ForegroundColor Yellow
    $Free2Email = Read-Host "Webhook Free 2 email"
}

if ($Free2Email) {
    $r = Register-WebhookApp -Label "Webhook Free 2" -Email $Free2Email
    $Results.Free2.ApiKey       = $r.ApiKey
    $Results.Free2.TriggerToken = $r.TriggerToken
    $Results.Free2.AppName      = $r.AppName
} elseif ($ParamMode) {
    Write-Host "[Webhook Free 2] No email specified — skipped." -ForegroundColor DarkGray
}

# =====================================================
# Test run (only for accounts with both keys present)
# =====================================================
$TestResults = @{}
$LabelMap    = @{ Pro = "Webhook Pro"; Free1 = "Webhook Free 1"; Free2 = "Webhook Free 2" }

Write-Host ""
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host " Test run" -ForegroundColor White
Write-Host "──────────────────────────────────────────────" -ForegroundColor DarkGray

foreach ($Key in @("Pro", "Free1", "Free2")) {
    $R = $Results[$Key]
    if ($R.ApiKey -and $R.TriggerToken) {
        $TestResults[$Key] = Test-WebhookApp `
            -Label         $LabelMap[$Key] `
            -TriggerToken  $R.TriggerToken `
            -ApiKey        $R.ApiKey
    }
}

# =====================================================
# Summary
# =====================================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " Environment variable summary" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

$AnyResult = $false

if ($Results.Pro.ApiKey) {
    $AnyResult  = $true
    $TestBadge  = if ($TestResults.ContainsKey("Pro")) { "[test: $($TestResults['Pro'])]" } else { "[test: skipped]" }
    $BadgeColor = if ($TestResults["Pro"] -eq "succeeded") { "Green" } else { "Yellow" }

    Write-Host ""
    Write-Host "# Webhook Pro  $TestBadge" -ForegroundColor $BadgeColor
    Write-Host "DIFY_WEBHOOK_PRO_TRIGGER_TOKEN=$($Results.Pro.TriggerToken)"
    Write-Host "DIFY_WEBHOOK_PRO_API_KEY=$($Results.Pro.ApiKey)"
}

if ($Results.Free1.ApiKey) {
    $AnyResult  = $true
    $TestBadge  = if ($TestResults.ContainsKey("Free1")) { "[test: $($TestResults['Free1'])]" } else { "[test: skipped]" }
    $BadgeColor = if ($TestResults["Free1"] -eq "succeeded") { "Green" } else { "Yellow" }

    Write-Host ""
    Write-Host "# Webhook Free 1  $TestBadge" -ForegroundColor $BadgeColor
    Write-Host "DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1=$($Results.Free1.TriggerToken)"
    Write-Host "DIFY_WEBHOOK_FREE_API_KEY_1=$($Results.Free1.ApiKey)"
}

if ($Results.Free2.ApiKey) {
    $AnyResult  = $true
    $TestBadge  = if ($TestResults.ContainsKey("Free2")) { "[test: $($TestResults['Free2'])]" } else { "[test: skipped]" }
    $BadgeColor = if ($TestResults["Free2"] -eq "succeeded") { "Green" } else { "Yellow" }

    Write-Host ""
    Write-Host "# Webhook Free 2  $TestBadge" -ForegroundColor $BadgeColor
    Write-Host "DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2=$($Results.Free2.TriggerToken)"
    Write-Host "DIFY_WEBHOOK_FREE_API_KEY_2=$($Results.Free2.ApiKey)"
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green

if (-not $AnyResult) {
    Write-Host "(all accounts skipped)" -ForegroundColor DarkGray
}

$FailedTests = $TestResults.GetEnumerator() | Where-Object { $_.Value -ne "succeeded" }
if ($FailedTests) {
    Write-Host ""
    Write-Host "WARNING: the following tests did not succeed." -ForegroundColor Red
    Write-Host "Verify the apps in the Dify Web UI before updating GitHub Secrets." -ForegroundColor Red
    foreach ($F in $FailedTests) {
        Write-Host "  - $($LabelMap[$F.Key]): $($F.Value)" -ForegroundColor Red
    }
}

if ($AnyResult) {
    Write-Host ""
    Write-Host "--------------------------------------------" -ForegroundColor Cyan
    Write-Host " Before running webhook-apps-step2-cutover.ps1" -ForegroundColor Cyan
    Write-Host "--------------------------------------------" -ForegroundColor Cyan
    Write-Host "Copy and paste the following into this session, then run webhook-apps-step2-cutover.ps1:" -ForegroundColor DarkGray

    if ($Results.Pro.ApiKey) {
        Write-Host ""
        Write-Host "`$env:DIFY_WEBHOOK_PRO_TRIGGER_TOKEN = `"$($Results.Pro.TriggerToken)`""
        Write-Host "`$env:DIFY_WEBHOOK_PRO_API_KEY        = `"$($Results.Pro.ApiKey)`""
    }
    if ($Results.Free1.ApiKey) {
        Write-Host ""
        Write-Host "`$env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1 = `"$($Results.Free1.TriggerToken)`""
        Write-Host "`$env:DIFY_WEBHOOK_FREE_API_KEY_1        = `"$($Results.Free1.ApiKey)`""
    }
    if ($Results.Free2.ApiKey) {
        Write-Host ""
        Write-Host "`$env:DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2 = `"$($Results.Free2.TriggerToken)`""
        Write-Host "`$env:DIFY_WEBHOOK_FREE_API_KEY_2        = `"$($Results.Free2.ApiKey)`""
    }
}
