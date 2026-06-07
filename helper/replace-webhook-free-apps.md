# How to Replace the Dify Apps for `webhook_free`

This guide walks you through migrating the `webhook_free` check to brand-new Dify apps
without causing a prolonged outage in the status history.

The check rotates between **two Free plan accounts** on every cycle to stay within API
rate limits:

- Cycle N: account A triggers → saves `trigger_id` and `account_index` to state file
- Cycle N+1: account A verifies → account B triggers → saves new state
- Cycle N+2: account B verifies → account A triggers → …

The `account_index` in the state file tells you which account made the **last trigger**.
The next GHA cycle will verify using that same account's API key, then trigger using the
other account.
So the manual trigger in step 5 must use the new credentials for `accounts[account_index]`.

---

## Prerequisites

- Access to both Dify Cloud Free (Sandbox) accounts
- Admin access to the GitHub repository secrets
- PowerShell (comes with Windows)

---

## Step 1 — Create new apps on both accounts

For **each** of the two Free plan accounts:

1. Log in to [Dify Cloud](https://cloud.dify.ai/).
2. Create a new **Workflow** app (you can import the DSL from `dsls/webhook.yml`).
3. Enable the **Webhook** trigger on the new app.
4. Do **not** delete or modify the old apps yet.

---

## Step 2 — Collect the new credentials

From each new app's settings page, record:

| Variable | Account | Where to find it |
| --- | --- | --- |
| `DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1` | Account 1 | Webhook trigger panel → token at the end of the trigger URL |
| `DIFY_WEBHOOK_FREE_API_KEY_1` | Account 1 | App settings → API keys |
| `DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2` | Account 2 | Webhook trigger panel → token at the end of the trigger URL |
| `DIFY_WEBHOOK_FREE_API_KEY_2` | Account 2 | App settings → API keys |

> The trigger URL shown in Dify looks like  
> `https://trigger.ai-plugin.io/triggers/webhook/<TRIGGER_TOKEN>`  
> Copy only the token part (the last path segment).

Keep these handy; you will need them in steps 5 and 6.

---

## Step 3 — Wait for the current GHA cycle to finish

Check the [Actions tab](../../actions) to confirm the latest `check.yml` run has completed.
This ensures the state file on the `main` branch reflects the most recent trigger.

---

## Step 4 — Pull the latest state and load the pending `trigger_id`

```powershell
git pull origin main
$state         = Get-Content data/.webhook_state/webhook_free.json | ConvertFrom-Json
$TRIGGER_ID    = $state.trigger_id
$ACCOUNT_INDEX = [int]$state.account_index
Write-Host "trigger_id:    $TRIGGER_ID"
Write-Host "account_index: $ACCOUNT_INDEX  (this account's new credentials will be used in step 5)"
```

The state file looks like:

```json
{"trigger_id": "status-check-f38ab85f4ebb", "triggered_at": "2026-06-07T13:30:49Z", "account_index": 1}
```

`$TRIGGER_ID` and `$ACCOUNT_INDEX` are now set in your session.
`account_index` is the account that made the last trigger and whose API key the next GHA
cycle will use to verify it.
Your goal is to make the same `trigger_id` appear in that account's new app's workflow logs
before the next cycle runs.

---

## Step 5 — Manually trigger the correct new app with the pending `trigger_id`

Fill in all four new credentials and run the block at once — the script selects the right
account automatically based on `$ACCOUNT_INDEX`:

```powershell
$tokens = @(
    @{ Token = "<NEW_DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1>"; ApiKey = "<NEW_DIFY_WEBHOOK_FREE_API_KEY_1>" },
    @{ Token = "<NEW_DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2>"; ApiKey = "<NEW_DIFY_WEBHOOK_FREE_API_KEY_2>" }
)
$TRIGGER_TOKEN = $tokens[$ACCOUNT_INDEX].Token
$NEW_API_KEY   = $tokens[$ACCOUNT_INDEX].ApiKey
$TIMESTAMP     = [int](Get-Date -UFormat %s)  # current Unix timestamp

Invoke-RestMethod -Method Post `
  -Uri "https://trigger.ai-plugin.io/triggers/webhook/$TRIGGER_TOKEN" `
  -ContentType "application/json" `
  -Body ($([pscustomobject]@{ id = $TRIGGER_ID; timestamp = $TIMESTAMP } | ConvertTo-Json -Compress))
```

Then wait until the workflow finishes (usually within a minute or two) and confirm it
succeeded:

```powershell
Invoke-RestMethod `
  -Uri "https://api.dify.ai/v1/workflows/logs?keyword=$TRIGGER_ID&limit=1" `
  -Headers @{ Authorization = "Bearer $NEW_API_KEY" } `
  | Select-Object -ExpandProperty data `
  | Select-Object -First 1 `
  | Select-Object -ExpandProperty workflow_run
```

Look for `status : succeeded` in the response.
If the status is still `running`, wait a moment and retry.
Do **not** proceed to step 6 until you see `succeeded`.

---

## Step 6 — Update the GitHub Actions secrets

You must complete this step **before the next GHA cycle runs** (cycles are spaced ~15 minutes apart).

1. Go to **Settings → Secrets and variables → Actions** in the GitHub repository.
2. Update all four secrets with the values collected in step 2:
   - `DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_1`
   - `DIFY_WEBHOOK_FREE_API_KEY_1`
   - `DIFY_WEBHOOK_FREE_TRIGGER_TOKEN_2`
   - `DIFY_WEBHOOK_FREE_API_KEY_2`

---

## What happens next

When the next GHA cycle runs:

1. **Check phase** — reads `account_index` from the state file, uses `accounts[account_index]`'s
   new API key, queries the **new** app's logs for `trigger_id`, finds the workflow you
   triggered manually → records **UP**.
2. **Trigger phase** — uses `accounts[(account_index + 1) % 2]`'s new token → fires the
   other account's new app → saves a new state file.

From that point on, both accounts rotate normally against the new apps.

---

## Rollback

If anything goes wrong before step 6, simply do nothing.
The next GHA cycle will query the old apps' logs (old secrets are still in place), get the
expected result, and continue normally — as if the migration never happened.

If step 6 was already completed but the new apps are not working, revert all four secrets
to the old values.  The state file will already contain a `trigger_id` that was sent to a
new app, so the very next cycle may record one **DOWN** result before recovering; this is
acceptable.
