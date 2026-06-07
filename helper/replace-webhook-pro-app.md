# How to Replace the Dify App for `webhook_pro`

This guide walks you through migrating the `webhook_pro` check to a brand-new Dify app
without causing a prolonged outage in the status history.
The check uses a **two-cycle mechanism**: cycle N triggers the webhook and saves the
`trigger_id` to a state file; cycle N+1 verifies that the workflow ran successfully.
The migration takes advantage of this by manually replaying the trigger on the new app
so the next automated cycle finds it in the new app's logs.

---

## Prerequisites

- Access to the Dify Cloud Pro workspace
- Admin access to the GitHub repository secrets
- PowerShell (comes with Windows)

---

## Step 1 — Create the new app

1. Log in to [Dify Cloud](https://cloud.dify.ai/) with the **Pro plan** account.
2. Create a new **Workflow** app (you can import the DSL from `dsls/webhook.yml`).
3. Enable the **Webhook** trigger on the new app.
4. Note that the new app must work independently — do **not** delete or modify the old app yet.

---

## Step 2 — Collect the new credentials

From the new app's settings page, record:

| Variable | Where to find it |
| --- | --- |
| `DIFY_WEBHOOK_PRO_TRIGGER_TOKEN` | Webhook trigger panel → token at the end of the trigger URL |
| `DIFY_WEBHOOK_PRO_API_KEY` | App settings → API keys |

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
$state      = Get-Content data/.webhook_state/webhook_pro.json | ConvertFrom-Json
$TRIGGER_ID = $state.trigger_id
Write-Host "trigger_id: $TRIGGER_ID"
```

The state file looks like:

```json
{"trigger_id": "status-check-f38ab85f4ebb", "triggered_at": "2026-06-07T13:30:49Z", "account_index": 0}
```

`$TRIGGER_ID` is now set in your session and will be used by the commands in step 5.
This ID was sent to the **old** app; the next GHA cycle will look it up via the API.
Your goal is to make the same ID appear in the **new** app's workflow logs before that cycle runs.

---

## Step 5 — Manually trigger the new app with the pending `trigger_id`

Replace the token placeholder and run:

```powershell
$TRIGGER_TOKEN = "<NEW_DIFY_WEBHOOK_PRO_TRIGGER_TOKEN>"
$TIMESTAMP     = [int](Get-Date -UFormat %s)  # current Unix timestamp

Invoke-RestMethod -Method Post `
  -Uri "https://trigger.ai-plugin.io/triggers/webhook/$TRIGGER_TOKEN" `
  -ContentType "application/json" `
  -Body ($([pscustomobject]@{ id = $TRIGGER_ID; timestamp = $TIMESTAMP } | ConvertTo-Json -Compress))
```

Then wait until the workflow finishes (usually within a minute or two) and confirm it succeeded:

```powershell
$NEW_API_KEY = "<NEW_DIFY_WEBHOOK_PRO_API_KEY>"

Invoke-RestMethod `
  -Uri "https://api.dify.ai/v1/workflows/logs?keyword=$TRIGGER_ID&limit=1" `
  -Headers @{ Authorization = "Bearer $NEW_API_KEY" } `
  | Select-Object -ExpandProperty data `
  | Select-Object -First 1 `
  | Select-Object -ExpandProperty workflow_run
```

Look for `"status": "succeeded"` in the response.
If the status is still `running`, wait a moment and retry.
Do **not** proceed to step 6 until you see `succeeded`.

---

## Step 6 — Update the GitHub Actions secrets

You must complete this step **before the next GHA cycle runs** (cycles are spaced ~15 minutes apart).

1. Go to **Settings → Secrets and variables → Actions** in the GitHub repository.
2. Update the following two secrets with the values collected in step 2:
   - `DIFY_WEBHOOK_PRO_TRIGGER_TOKEN`
   - `DIFY_WEBHOOK_PRO_API_KEY`

---

## What happens next

When the next GHA cycle runs:

1. **Check phase** — reads the state file, queries the **new** app's logs for the `trigger_id`
   using the **new** API key, finds the workflow that you triggered manually → records **UP**.
2. **Trigger phase** — fires the **new** app's webhook normally → saves a new state file.

From that point on, the check runs entirely against the new app.

---

## Rollback

If anything goes wrong before step 6, simply do nothing.
The next GHA cycle will query the old app's logs (old secrets are still in place), get the
expected result, and continue normally — as if the migration never happened.

If step 6 was already completed but the new app is not working, revert the two secrets to
the old values.  The state file will already contain a `trigger_id` that was sent to the
new app, so the very next cycle may record one **DOWN** result before recovering; this is
acceptable.
