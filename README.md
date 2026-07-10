# LifeHouse OS Beta Daily Email

FastAPI app for the LifeHouse OS beta daily email approval & send pipeline.

## Endpoints

- `POST /api/lhos/drafts` — Register a new draft for approval
- `GET /lhos/approve/{draft_id}` — Approval page with draft preview
- `POST /api/lhos/approve/{draft_id}` — Approve and send to all beta users
- `GET /api/lhos/drafts` — List all drafts
- `GET /api/lhos/drafts/{draft_id}` — Get draft details
- `GET /api/lhos/log` — Get send log
- `GET /health` — Health check

## Environment Variables

- `GOOGLE_CLIENT_ID` — Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` — Google OAuth client secret
- `GOOGLE_REFRESH_TOKEN` — Google OAuth refresh token
- `LHOS_APPROVERS` — JSON array of approver emails
- `LHOS_CONTACT_GROUP` — Google Contacts group name (default: "LifeHouse OS Beta - Active")
- `LHOS_SENDER_EMAIL` — Sender email (default: iris@lifehouseos.com)
- `LHOS_SENDER_NAME` — Sender name (default: LifeHouse OS)
- `LHOS_FEEDBACK_LINK` — Feedback link for beta users

## Google OAuth Bootstrap

After creating a Google Cloud OAuth Desktop client and downloading the JSON file:

```bash
python oauth_setup.py /Users/bobby/Downloads/client_secret_XXXX.json --push-railway
```

The helper requests Gmail send, Google Contacts readonly, and Drive readonly scopes. It saves a local
`google_token.json` file, validates the refresh token, checks the beta contact group, and pushes
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, and `LHOS_SENDER_EMAIL` to the linked
Railway production service. The JSON files are ignored by git.
