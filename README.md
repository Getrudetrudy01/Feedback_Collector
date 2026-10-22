# Feedback Collector

An embeddable feedback widget for websites, built on AWS Lambda, API Gateway, DynamoDB, and vanilla JavaScript. Each customer gets a **project**: a one-line `<script>` tag that adds a feedback button to their site, a hosted feedback page, and their own dashboard.

## Live Demo

The site is served over HTTPS through CloudFront; the addresses are the `WebsiteUrl` and `WidgetUrl` stack outputs.

- **Dashboard**: `WebsiteUrl/admin.html` (sign in with the owner token or a project admin key)
- **Widget script**: `WebsiteUrl/widget.js`

Each project's hosted feedback page and widget preview are linked from its **Install & settings** panel in the dashboard.

## Features

**For your customers' visitors**
- **Embeddable widget**: a floating feedback button with category, optional star rating, message, and optional email. It's isolated in a Shadow DOM, so the host site's CSS can't break it, and it works with keyboard and on phones
- **Hosted feedback page** for each project, to share as a link
- The page the feedback was sent from is recorded automatically
- **Bug reports with context**: choosing "Something is broken" offers a screenshot (capture the current tab, or attach an image) and includes the page's recent JavaScript errors. Every report includes the browser, screen and window size, language, and time zone

**For your customers (project admins)**
- **Their own dashboard**, signed in with their project admin key: totals, average rating, unresolved count, rating breakdown, search and filters, resolve / reopen, CSV export
- **Screenshots and browser details on each card**: browser and OS, sizes, language, time zone, and the page errors that happened before the report
- **Install panel** with the embed snippet, hosted page link, and a live widget preview
- **Allowed websites**: restrict which sites can send feedback through their widget
- **Key rotation**: replace a leaked admin key instantly

**For you (the owner)**
- **Projects page**: create projects, see all of them, and open any project's dashboard
- **Email alerts** for projects you opt in, optionally only for low ratings (Amazon SNS)
- **Spam protection**: hidden honeypot field, per-project origin checks, and API rate limits
- **Infrastructure as code**, tests, and CI

## Embedding the widget

Paste the snippet from the project's **Install & settings** panel before `</body>`:

```html
<script src="https://YOUR_SITE/widget.js"
        data-project="proj_..."
        data-api="https://YOUR_API/v1"
        async></script>
```

| Attribute       | Default            | Purpose                       |
|-----------------|--------------------|-------------------------------|
| `data-color`    | `#1976d2`          | Button and accent color (hex) |
| `data-position` | `right`            | `right` or `left`             |
| `data-label`    | `Feedback`         | Button text                   |
| `data-title`    | `Send us feedback` | Panel heading                 |

On sites with signed-in users, attach their details so they don't have to type them:

```js
FeedbackWidget.identify({ name: 'Pat Lee', email: 'pat@example.com' });
FeedbackWidget.open();   // open from your own button or link; also .close()
```

## How keys work

| Credential | Looks like | Who has it | What it can do |
|---|---|---|---|
| Project ID | `proj_1a3fde02822e` | Public, in the snippet | Submit feedback (only from the project's allowed websites, if any are set) |
| Project admin key | `sk_proj_..._...` | The customer | Read and manage that one project's feedback and settings |
| Owner token | your secret | You | Create projects, see every project, turn on email alerts |

Admin keys are shown once, when a project is created or its key is rotated. Only a hash is stored.

## Architecture

```
Customer site ──► widget.js (from the S3 website)
      │
      ├──► API Gateway (HTTP API, /v1) ──► Lambda (backend/) ──► DynamoDB (Projects, ProjectFeedback)
      │                                             ├──► SNS (owner email alerts)
      │                                             └──► signs upload/view links for the screenshots bucket
      └──► S3 screenshots bucket (private; direct upload with a presigned post)
```

### Screenshots

1. The widget asks `POST /v1/projects/{projectId}/screenshots` for an upload slot (origin-checked like submissions).
2. It uploads the image straight to the private screenshots bucket with the returned presigned post. That permission lasts 10 minutes, allows JPEG, PNG, or WebP only, and up to 5 MB.
3. It submits the feedback with the `screenshotId`. The API moves the image from `pending/` to `screenshots/`.
4. The dashboard shows it through a view link that expires after an hour.

Uploads that never get attached to feedback are deleted from `pending/` after a day by a lifecycle rule. The widget re-encodes every image as JPEG first, which also strips photo metadata such as GPS location. Screen capture uses the browser's own screen-sharing prompt, so nothing is captured without the visitor choosing to share.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST  | `/v1/projects` | owner | Create a project. Returns its admin key once |
| GET   | `/v1/projects` | owner | List projects |
| GET   | `/v1/projects/{projectId}` | owner or project key | Get a project |
| PATCH | `/v1/projects/{projectId}` | owner or project key | Update `name`, `allowedOrigins`; owner only: `notifyOwner` |
| POST  | `/v1/projects/{projectId}/rotate-key` | owner or project key | Replace the admin key |
| POST  | `/v1/projects/{projectId}/screenshots` | none (origin-checked) | Get a one-time screenshot upload slot |
| POST  | `/v1/projects/{projectId}/feedback` | none (origin-checked) | Submit feedback (optional `context`, `screenshotId`) |
| GET   | `/v1/projects/{projectId}/feedback` | owner or project key | List feedback, newest first (`limit`, `nextToken`) |
| PATCH | `/v1/projects/{projectId}/feedback/{feedbackId}` | owner or project key | Set `status` to `new` or `resolved` |

Authenticated requests send `Authorization: Bearer <owner token or admin key>`.

## Project Structure

```
feedback_collector/
├── backend/
│   ├── lambda_function.py    # Routes requests to the handlers below
│   ├── projects.py           # Project create / read / update / key rotation
│   ├── feedback.py           # Submitting, listing, and resolving feedback; alerts
│   ├── screenshots.py        # Screenshot upload slots, attaching, and view links
│   ├── auth.py               # Owner token and admin key checks
│   ├── common.py             # Responses, errors, and validation helpers
│   └── settings.py           # Environment config and AWS clients
├── frontend/
│   ├── widget.js             # The embeddable widget
│   ├── index.html, app.js    # Hosted feedback page (?project=ID)
│   ├── admin.html, admin.js  # Owner and customer dashboard
│   ├── demo.html             # Widget preview page
│   ├── style.css             # Hosted page styles
│   └── config.js             # API URL setting
├── tests/                    # pytest tests (AWS mocked with moto)
├── template.yaml             # AWS SAM template for the whole stack
└── .github/workflows/ci.yml  # Lint + test on every push and pull request
```

## Deploying

You need an AWS account and the [AWS CLI](https://aws.amazon.com/cli/), signed in (`aws sts get-caller-identity` should show your account). The commands below are for **PowerShell**; in bash, replace the trailing backticks with `\`.

1. **Make an owner token** and save it in a password manager:

   ```powershell
   $token = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
   $token
   ```

2. **Package and deploy.** The artifacts bucket holds the Lambda code; create it once with `aws s3 mb s3://feedback-collector-artifacts-YOUR_ACCOUNT_ID`.

   ```powershell
   aws cloudformation package `
     --template-file template.yaml `
     --s3-bucket feedback-collector-artifacts-YOUR_ACCOUNT_ID `
     --output-template-file packaged.yaml

   aws cloudformation deploy `
     --template-file packaged.yaml `
     --stack-name feedback-collector `
     --region us-east-1 `
     --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND `
     --parameter-overrides "OwnerToken=$token" "NotificationEmail=you@example.com"
   ```

   `NotificationEmail` is optional (confirm the email AWS sends). Other optional parameters: `NotifyMaxRating` (e.g. `2` to alert only on 1–2 stars), `ThrottleRateLimit`, `ThrottleBurstLimit`.

3. **Show the outputs:**

   ```powershell
   aws cloudformation describe-stacks --stack-name feedback-collector --region us-east-1 `
     --query "Stacks[0].Outputs" --output table
   ```

4. **Point the frontend at the API.** Set `apiUrl` in `frontend/config.js` to the `ApiUrl` output (it ends in `/v1`).

5. **Upload the frontend:**

   ```powershell
   aws s3 sync frontend/ s3://YOUR_FrontendBucketName/
   ```

6. **Create your first project.** Open `WebsiteUrl/admin.html` (the `https://` address), sign in with the owner token, and create a project. Copy its admin key and snippet when shown.

Re-run steps 2 and 5 after code changes. CloudFront caches files for up to 5 minutes; to see changes immediately, clear its cache with the `DistributionId` output:

```powershell
aws cloudfront create-invalidation --distribution-id YOUR_DistributionId --paths "/*"
```

Always copy snippets from the `https://` dashboard: secure websites block scripts loaded over `http://`, and browsers only allow screen capture on secure pages. The older `http://...s3-website...` address still works, but the dashboard warns when it's used.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest              # run tests
ruff check .        # lint Python
cfn-lint template.yaml
```

## Notes

- **Allowed websites** rely on the `Origin` header, which browsers always send and pages can't fake. Scripts outside a browser can still send any origin, so treat this as stopping other *websites* from using a project, not as authentication. Rate limits cap abuse beyond that.
- The service's own website (hosted page and widget preview) can always submit to any project.
- **Privacy:** screenshots can show anything that was on the visitor's screen, and page errors can contain page details. Both are only collected for bug reports, and the widget tells visitors what is sent. Screenshots are kept until deleted from the bucket; deleting individual feedback from the dashboard is planned.
- Screen capture works in desktop Chrome, Edge, Firefox, and Safari. On phones the widget offers "Attach an image" only.
- Email alerts go to the owner's address. Per-customer alerts, self-serve signup, and billing are planned for a later milestone.
