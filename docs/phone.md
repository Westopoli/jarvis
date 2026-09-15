# Phone call setup (Twilio + Tailscale Funnel)

One-time, ~15 minutes. Telnyx routes also exist (`/texml`, `/ws/telnyx`);
the steps below are for Twilio.

## 1. Secrets file

`.env` in the repo root (gitignored, never committed). Fill in:

```
TWILIO_ACCOUNT_SID=AC...            # Twilio console home
TWILIO_AUTH_TOKEN=...               # same card, eye icon
JARVIS_ALLOWED_CALLER=+1XXXXXXXXXX  # your mobile, E.164; comma-separate several
JARVIS_PUBLIC_HOSTNAME=<machine>.<tailnet>.ts.net
```

## 2. Publish only the phone routes

Funnel must be enabled for the tailnet (admin console -> DNS -> HTTPS
certificates on; Access Controls -> `nodeAttrs` granting `funnel`). Then:

```
tailscale funnel --bg --set-path=/twiml     http://127.0.0.1:8000/twiml
tailscale funnel --bg --set-path=/ws/twilio http://127.0.0.1:8000/ws/twilio
tailscale funnel --bg --set-path=/health    http://127.0.0.1:8000/health
tailscale funnel status
```

`/events` (Claude hook ingest) is deliberately not published.

## 3. Twilio

1. Buy a number: Phone Numbers -> Buy a number -> Voice capability, any US
   local number (~$1.15/month, trial credit covers it). A trial account can
   only call/receive from verified numbers; your mobile gets verified at
   signup, which is all Jarvis needs. Trial calls start with a short
   "trial account" announcement; upgrading removes it.
2. Phone Numbers -> Active numbers -> your number -> Voice configuration:
   - A call comes in: Webhook, `https://<JARVIS_PUBLIC_HOSTNAME>/twiml`, HTTP POST
   - Leave the fallback blank.
3. Save the number as a contact on the phone ("Jarvis") so Siri can dial it.

The webhook is signed; Jarvis verifies `X-Twilio-Signature` with the auth
token and rejects anything else. Callers not in `JARVIS_ALLOWED_CALLER` get
`<Reject/>` before any audio flows.

## 4. Run

```
uv run jarvis            # reads .env itself            # or: systemctl --user enable --now jarvis
curl https://$JARVIS_PUBLIC_HOSTNAME/health
```

Call the number. First audio ~2-3 s after pickup (models load per call).

## What to expect on the call

- Full duplex: the phone cancels its own echo, so you can talk over Jarvis.
  Three or more words interrupt him; a honk does not.
- Say "Jarvis" first if it has been quiet for more than 20 s.
- Latency is roughly the desktop numbers plus phone network (~0.3 s).
- Jarvis renames your tmux windows to unique names on session start
  (`<project>` for shells, `<project>-claude` for Claude sessions). Set
  `JARVIS_RENAME_WINDOWS=0` to stop that.
