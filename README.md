# iCal → Google Calendar Sync (uv Edition)

Daily job that pulls one or more iCal schedules and mirrors them into Google Calendar. Runs serverless on AWS Lambda + EventBridge Scheduler. Expected cost is $0–$0.05/month (well inside the AWS free tier).

How it stays reliable without a database: every event is pushed to Google via `events.import()` keyed on a prefixed `iCalUID`. Google itself creates the event if the UID is new, or updates it in place if it already exists — so re-running the sync never creates duplicates. Events removed from the source feed are cleaned up by comparing the current feed's UIDs against a tagged set of previously-synced events on the calendar.

---

## 1. Local Development Setup (uv)

This project uses [uv](https://github.com/astral-sh/uv) for fast package management and virtual environment configuration.

1. Install `uv` if you haven't already.
2. Initialize and synchronize the local virtual environment (installs development dependencies like `boto3` for local imports while keeping them out of the production Lambda package):
   ```bash
   uv sync
   ```
3. Run local unit tests to verify the sync and fallback parser logic:
   ```bash
   uv run python -m unittest discover -s . -p "test_*.py"
   ```

---

## 2. Google Cloud Setup

You need a Google Cloud project, the Calendar API enabled, and a service account whose key gets stored in AWS (never in Google Cloud itself — no Google Cloud costs are involved beyond this one-time setup).

1. Go to https://console.cloud.google.com/ and create a new project.
2. Enable the Calendar API: go to **APIs & Services → Library**, search **Google Calendar API**, and click **Enable**.
3. Create a service account: **APIs & Services → Credentials → Create Credentials → Service account**. Give it any name, e.g. `aws-ical-sync`. No roles/permissions need to be granted at the project level.
4. Open the service account, go to the **Keys** tab → **Add Key → Create new key → JSON**. This downloads a `.json` key file — keep it, you'll paste its contents into AWS in the next step.
5. Note the service account's email address (e.g. `aws-ical-sync@your-project.iam.gserviceaccount.com`).

**Share your calendar with the service account:**
1. Open Google Calendar → find the calendar you want events added to (or use your main calendar) → **Settings and sharing**.
2. Under **Share with specific people**, add the service account's email with permission **"Make changes to events."**
3. Note the **Calendar ID** shown further down that settings page (for your primary calendar this is just your Gmail address; for a secondary calendar it looks like `abc123@group.calendar.google.com`).

---

## 3. Store the Service Account Key in AWS

Using the `.json` key file downloaded above, store it in AWS Systems Manager Parameter Store:

```bash
aws ssm put-parameter \
  --name "/ical-sync/google-service-account" \
  --type SecureString \
  --value file://path/to/your-service-account-key.json \
  --region eu-central-2
```

Use the same region here as in `deploy.sh`.

---

## 4. Configure and Deploy

Open `deploy.sh` and edit the config block at the top:

- `REGION` — an AWS region close to you.
- `SSM_PARAM_NAME` — path to the SSM parameter created in step 3.
- `SCHEDULE_EXPRESSION` — daily cron schedule (defaults to daily at 05:00 UTC).

Create a `sync_configs.py` file in the project root with your feed configurations:

### sync_configs.py Example:
```python
CONFIGS = [
  {
    "ical_url": "https://app.myice.hockey/api/players/ical/50553/113",
    "calendar_id": "primary",
    "uid_prefix": "hockey1-",
    "summary_format": "🏒 {summary}",
    "color_id": "11"
  },
  {
    "ical_url": "https://app.myice.hockey/api/players/ical/50553/114",
    "calendar_id": "primary",
    "uid_prefix": "hockey2-",
    "summary_format": "{summary} (away)",
    "color_id": "5"
  }
]
```

### Configuration Options:
- `ical_url` (required) — URL of the iCal feed to sync
- `calendar_id` (required) — Google Calendar ID (use "primary" for your main calendar)
- `uid_prefix` (default: "ical-") — Prefix to namespace UIDs, must be unique per feed
- `summary_format` (default: "{summary}") — Format string for event titles, use `{summary}` as placeholder
- `color_id` (optional) — Google Calendar color ID 1-11 (see lambda_function.py COLOR_REFERENCE)

> [!IMPORTANT]
> **Use unique `uid_prefix` values** for each configured feed! This isolates their events so that the sync process for one feed doesn't conflict-delete the events synced by another feed.

### Deploy:
```bash
./deploy.sh
```

This script will:
1. Package the Lambda using `uv` (with automatic fallback to `pip` if `uv` is missing).
2. Create an IAM role scoped to read the Google Service Account key SSM parameter and write CloudWatch logs.
3. Create/update the Lambda function and set the `SYNC_CONFIGS` environment variable.
4. Create the daily EventBridge rule to trigger the sync.

---

## 5. Test It

Trigger the sync manually:

```bash
aws lambda invoke \
  --function-name aws-ical-sync \
  --region eu-central-2 \
  --log-type Tail \
  out.json
cat out.json
```

You should see a list of results summarizing imported and deleted events for each configured feed. Check your Google Calendar to verify the events have appeared.

To tail logs:
```bash
aws logs tail /aws/lambda/aws-ical-sync --region eu-central-2 --since 1d
```

---

## Cost Breakdown

| Resource | Usage | Cost |
|---|---|---|
| Lambda | ~30 invocations/month, <15s each | Free tier (1M req/month free) |
| EventBridge rule | 1 daily trigger | Free |
| SSM Parameter Store | 1 SecureString param | Free |
| CloudWatch Logs | small log volume | Free tier |

Total: **$0/month**.
