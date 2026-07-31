# LifeHouse OS Beta Daily Email

FastAPI app for the LifeHouse OS beta daily email approval & send pipeline.

## Endpoints

- `POST /api/lhos/drafts` — Register a new draft for approval
- `GET /lhos/approve/{draft_id}` — Read-only draft preview; production approval is not accepted here
- `GET /api/lhos/security-policy` — Read-only approval and Iris feature policy
- `GET /api/lhos/automation/status` — Current daily automation state
- `GET /api/lhos/drafts` — List all drafts
- `GET /api/lhos/drafts/{draft_id}` — Get draft details
- `GET /api/lhos/log` — Get send log
- `GET /health` — Health check


## Iris Creative Fallback

When no complete dated human content is available by **7:30 AM America/New_York**, Iris creates an original LifeHouse OS Daily Briefing for review:

- A rotating 28-topic household theme prevents repetitive daily prompts.
- The configured GLM model develops a fresh, practical editorial angle and beta-testing mission.
- Optional authenticated references are treated as untrusted thematic context, never as product facts or model instructions.
- Model JSON is normalized into the application-owned email structure and checked for branding, length, distinct sections, concrete action, unsafe advice, external links, placeholders, and unverified product claims.
- Creative-provider output must use exactly six sections in order: Today’s Beta Notes, A Household Experiment, Today’s Beta Mission, Helpful Reminder, A Question for Your House, and Thank You.
- Missing credentials, provider errors, malformed JSON, or failed validation automatically use the validated curated briefing instead of missing the review window.
- Iris-created content is **review-only**. It cannot reach beta recipients without an authenticated, allow-listed reply bound to the exact current review email.
- Valid human content remains authoritative and supersedes generated content through the durable delivery-start boundary.
- `dry_run=true` reports what would happen without calling the model, writing state, creating a draft, or sending email.

The active generator and safe-failover policy are visible through `GET /api/lhos/security-policy`; generated draft provenance is stored in the daily automation state.

## Environment Variables

- `GOOGLE_CLIENT_ID` — Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` — Google OAuth client secret
- `GOOGLE_REFRESH_TOKEN` — Google OAuth refresh token
- `LHOS_APPROVERS` — JSON array of approver emails
- `LHOS_CONTACT_GROUP` — Google Contacts group name (default: "LifeHouse OS Beta - Active")
- `LHOS_SENDER_EMAIL` — Sender email (default: iris@lifehouseos.com)
- `LHOS_SENDER_NAME` — Sender name (default: LifeHouse OS)
- `LHOS_FEEDBACK_LINK` — Feedback link for beta users
- `GLM_API_KEY` — Z.AI/GLM credential used for Iris creative generation and authenticated review revisions
- `GLM_BASE_URL` — OpenAI-compatible GLM API base (default: `https://api.z.ai/api/paas/v4`)
- `IRIS_CREATIVE_MODEL` — Creative fallback model (default: `glm-4.7-flash`)

## Google OAuth Bootstrap

After creating a Google Cloud OAuth Desktop client and downloading the JSON file:

```bash
python oauth_setup.py /Users/bobby/Downloads/client_secret_XXXX.json --push-railway
```

The helper requests Gmail send, Google Contacts readonly, and Drive readonly scopes. It saves a local
`google_token.json` file, validates the refresh token, checks the beta contact group, and pushes
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, and `LHOS_SENDER_EMAIL` to the linked
Railway production service. The JSON files are ignored by git.
