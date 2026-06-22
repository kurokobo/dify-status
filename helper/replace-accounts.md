# Replacing Accounts and Webhook Apps

Run all scripts from the **repository root** in the same terminal session.

---

## Replacing the Pro Account

Follow these steps when the Pro plan subscription changes and you need to switch to a new Dify Pro account.

### Step 1 — Create apps on the new Pro account

For each check below, create a new app on the new Pro account by importing the DSL file via the Dify Cloud web UI or PSDify. Note the API key for each.

| Check | DSL file | GitHub Secret |
| --- | --- | --- |
| API | `dsls/api.yml` | `DIFY_API_KEY` |
| Sandbox | `dsls/sandbox.yml` | `DIFY_SANDBOX_API_KEY` |
| Plugin | `dsls/plugin.yml` | `DIFY_PLUGIN_API_KEY` |

### Step 2 — Create Knowledge bases on the new Pro account

**Knowledge Retrieval** (`DIFY_RETRIEVE_DATASET_ID` / `DIFY_RETRIEVE_API_KEY`)  
Create a Knowledge base named **`status: retrieve`** and upload `hello_world.txt` with **High-Quality** indexing.  
Note the Dataset ID and a Knowledge API key.

**Knowledge Indexing (Pro Plan)** (`DIFY_INDEXING_PRO_DATASET_ID` / `DIFY_INDEXING_PRO_API_KEY`)  
Create a Knowledge base named **`status: indexing`** and upload `hello_world.txt`  with Economy indexing.  
Note the Dataset ID and a Knowledge API key.

### Step 3 — Update GitHub Secrets (non-Webhook)

```powershell
gh secret set DIFY_API_KEY                 --body "<api key>"
gh secret set DIFY_SANDBOX_API_KEY         --body "<api key>"
gh secret set DIFY_PLUGIN_API_KEY          --body "<api key>"
gh secret set DIFY_RETRIEVE_DATASET_ID     --body "<dataset id>"
gh secret set DIFY_RETRIEVE_API_KEY        --body "<api key>"
gh secret set DIFY_INDEXING_PRO_DATASET_ID --body "<dataset id>"
gh secret set DIFY_INDEXING_PRO_API_KEY    --body "<api key>"
```

### Step 4 — Replace the Webhook Pro app

Follow the **Webhook Pro** section below.

---

## Replacing Webhook Apps

### Webhook Pro

**Step 1** — Create the new app. When it finishes, copy-paste the printed `$env:` lines into the session.

```powershell
.\helper\webhook-apps-step1-create.ps1 -ProEmail <email>
```

**Step 2** — Wait for the current GHA cycle to complete ([Actions tab](../../actions)).

**Step 3** — Replay the pending trigger and get the new secrets.

```powershell
.\helper\webhook-apps-step2-cutover.ps1 -Pro
```

Run the printed `gh secret set` commands before the next GHA cycle (~15 min apart).

---

### Webhook Free

**Step 1** — Create new apps on both accounts. Copy-paste the printed `$env:` lines.

```powershell
.\helper\webhook-apps-step1-create.ps1 -Free1Email <email1> -Free2Email <email2>
```

**Step 2** — Wait for the current GHA cycle to complete ([Actions tab](../../actions)).

**Step 3** — Replay the pending trigger for the correct account and get the new secrets.

```powershell
.\helper\webhook-apps-step2-cutover.ps1 -Free
```

Run the printed `gh secret set` commands before the next GHA cycle (~15 min apart).

---

## Notes

- Do **not** delete old apps or discard old secrets until the next GHA cycle confirms **UP** with the new credentials.
- **Rollback before updating secrets:** do nothing — the old secrets are still in place.
- **Rollback after updating secrets:** revert each secret to its old value with `gh secret set`. The next cycle may record one **DOWN** before recovering.
