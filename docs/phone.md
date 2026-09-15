# Phone call setup (Telnyx + Tailscale Funnel)

One-time, ~15 minutes.

## 1. Secrets file

`~/.config/jarvis/env` (mode 600, outside the repo). Fill in:

```
TELNYX_API_KEY=KEY...          # Telnyx portal -> API Keys -> Create
TELNYX_ALLOWED_CALLER=+1XXXXXXXXXX   # your mobile, E.164
JARVIS_PUBLIC_HOSTNAME=<machine>.<tailnet>.ts.net
```

## 2. Publish the two phone routes

Funnel must be enabled for this tailnet (admin console -> DNS -> HTTPS
certificates on; Access Controls -> `nodeAttrs` with `funnel`). Then:

```
tailscale funnel --bg --set-path=/texml     http://127.0.0.1:8000/texml
tailscale funnel --bg --set-path=/ws/telnyx http://127.0.0.1:8000/ws/telnyx
tailscale funnel --bg --set-path=/health    http://127.0.0.1:8000/health
tailscale funnel status
```

`/events` (Claude hook ingest) is deliberately not published.

## 3. Telnyx

1. Buy a US local number (Numbers -> Search & Buy).
2. Voice -> TeXML Applications -> Create:
   - Webhook URL: `https://<JARVIS_PUBLIC_HOSTNAME>/texml`, method POST
   - Leave failover blank.
3. Assign the number to that TeXML application (Numbers -> your number ->
   Voice -> Connection/Application).
4. Save the number as a contact on the phone ("Jarvis") so Siri can dial it.

## 4. Run

```
set -a; source ~/.config/jarvis/env; set +a
uv run jarvis            # or: systemctl --user enable --now jarvis
curl https://$JARVIS_PUBLIC_HOSTNAME/health
```

Call the number. First audio ~2 s after pickup (models load per call).
Calls from any number not in `TELNYX_ALLOWED_CALLER` are dropped before a
pipeline is built.

## What to expect on the call

- Full duplex: the phone cancels its own echo, so you can talk over Jarvis.
  Three or more words interrupt him; a honk does not.
- Say "Jarvis" first if it has been quiet for more than 20 s.
- Latency is roughly the desktop numbers plus phone network (~0.3 s).
