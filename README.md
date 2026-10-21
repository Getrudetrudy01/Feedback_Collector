# Feedback Collector

A serverless feedback collection system built with AWS Lambda, API Gateway, DynamoDB, and vanilla JavaScript. Users submit feedback through a web form; admins sign in to a dashboard to view, filter, and export it.

## Live Demo

- **Feedback Form**: http://feedback-collector-frontendbucket-9ucyy7ouo0cv.s3-website-us-east-1.amazonaws.com/
- **Admin Dashboard**: http://feedback-collector-frontendbucket-9ucyy7ouo0cv.s3-website-us-east-1.amazonaws.com/admin.html (requires the admin token)

## Features

- **Feedback form** with name, email, category, 1–5 star rating, and message
- **Server-side validation** with clear error messages
- **Token-protected admin dashboard** with total, average rating, unresolved count, and auto-refresh every 30 seconds
- **Search and filters** by text, rating, category, and status, plus a clickable rating breakdown chart
- **Resolve / reopen** each piece of feedback to track what's been handled
- **Email alerts** for new feedback (optionally only low ratings) via Amazon SNS
- **Spam protection**: a hidden honeypot field and API rate limiting
- **CSV export** of the filtered view, safe to open in spreadsheet apps
- **Infrastructure as code** (AWS SAM): one command deploys the whole stack
- **Tests and CI**: pytest + moto, ruff, and cfn-lint run on every pull request

## Architecture

```
Browser ──► S3 static website (frontend/)
   │
   └──► API Gateway (HTTP API) ──► Lambda (backend/) ──► DynamoDB
```

| Method | Path             | Auth        | Purpose                                         |
|--------|------------------|-------------|-------------------------------------------------|
| POST   | `/feedback`      | none        | Submit feedback                                 |
| GET    | `/feedback`      | admin token | List feedback, one page at a time               |
| PATCH  | `/feedback/{id}` | admin token | Set status: `{"status": "new" \| "resolved"}`   |

Admin requests send the token as `Authorization: Bearer <token>`.

`GET /feedback` accepts `limit` (1–500, default 100) and `nextToken`. The response is `{"feedbacks": [...], "nextToken": "..."}`; keep passing `nextToken` back until it is `null`. The dashboard does this automatically.

New feedback publishes to an SNS topic. With `NotificationEmail` set, you get an email for every submission, or only for low ratings if you set `NotifyMaxRating` (e.g. `2` alerts on 1–2 star feedback only).

## Project Structure

```
feedback_collector/
├── backend/
│   └── lambda_function.py    # API handler (validation, auth, DynamoDB access)
├── frontend/
│   ├── index.html            # Feedback form
│   ├── app.js                # Form logic
│   ├── style.css             # Form styles
│   ├── admin.html            # Admin dashboard
│   ├── admin.js              # Dashboard logic
│   └── config.js             # API URL setting
├── tests/                    # pytest tests (DynamoDB mocked with moto)
├── docs/iam-policy.json      # Least-privilege policy for manual deployments
├── template.yaml             # AWS SAM template for the whole stack
└── .github/workflows/ci.yml  # Lint + test on every push and pull request
```

## Deploying

Prerequisites: an AWS account, the [AWS CLI](https://aws.amazon.com/cli/), and the [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

1. **Deploy the backend and bucket.** Pick a long random admin token (for example `openssl rand -hex 24`):

   ```bash
   sam build
   sam deploy --guided --parameter-overrides AdminToken=YOUR_LONG_RANDOM_TOKEN
   ```

   Optional parameters: `NotificationEmail=you@example.com` (confirm the email AWS sends you), `NotifyMaxRating=2`, `ThrottleRateLimit` and `ThrottleBurstLimit` (requests per second).

   Note the `ApiUrl`, `WebsiteUrl`, and `FrontendBucketName` outputs.

2. **Point the frontend at your API.** Set `apiUrl` in `frontend/config.js` to the `ApiUrl` output.

3. **Upload the frontend:**

   ```bash
   aws s3 sync frontend/ s3://YOUR_FRONTEND_BUCKET_NAME/
   ```

4. **Lock CORS to your site.** Redeploy with the website URL as the allowed origin:

   ```bash
   sam deploy --parameter-overrides AdminToken=YOUR_LONG_RANDOM_TOKEN AllowedOrigin=http://YOUR_BUCKET.s3-website-us-east-1.amazonaws.com
   ```

Open `WebsiteUrl` for the form and `WebsiteUrl/admin.html` for the dashboard.

> The stack creates a **new** DynamoDB table. To keep data from an existing manually created `FeedBackTable`, either copy it over or skip the template and deploy manually (below).

### Manual deployment

If you set up resources in the AWS console instead:

- Lambda runtime: Python 3.13, handler `lambda_function.lambda_handler`, code from `backend/`
- Environment variables: `TABLE_NAME`, `ADMIN_TOKEN` (required for the dashboard), `ALLOWED_ORIGINS` (comma-separated, or `*`), and optionally `NOTIFY_TOPIC_ARN` and `NOTIFY_MAX_RATING`
- Execution role: attach `docs/iam-policy.json` (replace the account ID and table name). If you use alerts, also allow `sns:Publish` on the topic
- API Gateway HTTP API routes: `POST /feedback`, `GET /feedback`, and `PATCH /feedback/{id}` to the Lambda

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest              # run tests
ruff check .        # lint Python
cfn-lint template.yaml
```

To try the frontend locally, serve the folder (`python -m http.server -d frontend 8000`) and open http://localhost:8000. Make sure `ALLOWED_ORIGINS` includes that origin or is `*`.

## Security Notes

- Feedback submitted before categories and statuses existed shows as "General" and "Unresolved".
- The admin token is a shared secret. Anyone who has it can read all feedback, which includes names and emails. Rotate it by redeploying with a new `AdminToken`.
- For multiple admins or per-user access, replace the token with Amazon Cognito and an API Gateway JWT authorizer.
