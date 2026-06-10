# Replacing Webhook Apps

Run both scripts from the **repository root** in the same terminal session.

---

## Webhook Pro

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

## Webhook Free

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

- Do **not** delete the old apps until the next GHA cycle confirms **UP** with the new app.
- **Rollback before updating secrets:** do nothing — the old secrets are still in place.
- **Rollback after updating secrets:** revert the secrets to the old values. The next cycle may record one **DOWN** before recovering.
