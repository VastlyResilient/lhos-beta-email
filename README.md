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
- `LHOS_SENDER_EMAIL` — Sender email (default: iris@lifehouseos.app)
- `LHOS_SENDER_NAME` — Sender name (default: LifeHouse OS)
- `LHOS_FEEDBACK_LINK` — Feedback link for beta users
