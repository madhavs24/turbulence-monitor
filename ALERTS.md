# Band-shift email alerts

## Setup (one-time)

1. Create a free account at [resend.com](https://resend.com) and verify a sender address.
2. Add GitHub repo secrets on `madhavs24/turbulence-monitor`:
   - `RESEND_API_KEY` — API key from Resend
   - `RESEND_FROM` — verified sender, e.g. `onboarding@resend.dev` or `alerts@yourdomain.com`
3. Add subscriber emails to [`data/alert_subscribers.txt`](data/alert_subscribers.txt) (one per line).
   - Hero form submissions appear in the Netlify dashboard under **Forms → band-alerts**.

## How it works

After each daily `refresh-snapshot` run, [`tools/notify_band_change.py`](tools/notify_band_change.py) compares
today's `outlook.turbulence_band` to [`web/static/alert_state.json`](web/static/alert_state.json).
On a change (e.g. Calm → Elevated), subscribers receive a plain-text email. No buy/sell language.

If secrets are missing, the step logs a skip and does not fail the workflow.
