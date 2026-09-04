# pysence-standalone

A standalone, fully-customizable Discord Rich Presence. Built as a
generalization of [ropysence](https://github.com/codedcorerblx/ropysence):
same OAuth2 + real Gateway connection under the hood, but with the
Roblox-specific monitoring stripped out entirely.

**Monitors nothing by default.** There's no external account or service
this polls unless you explicitly wire one up yourself via an `api.*` field
in `options.txt` (see below). With no such field, it authorizes once,
sends a single presence update, and just keeps the Gateway socket alive.

**Self-hosted, single-user**, same as the project it's based on: you
create your own Discord application and authorize it against your own
account. No shared server, no shared credentials.

## Setup

```
pip install -r requirements.txt
python run.py
```

Or install it as a command:

```
pip install -e .
pysence-standalone
```

1. Create a Discord app at <https://discord.com/developers/applications>,
   enable OAuth2 → "Public Client", and add a redirect URI matching
   `script.localhost.port` (default `http://127.0.0.1:8969/callback`).
2. Run once: creates `options.txt` **in this project directory** and exits
   so you can fill in `script.user.id`.
3. Run again: opens your browser for Discord authorization (cached
   afterward), then connects and starts sending your configured presence.

Preview what will be sent without touching Discord at all:

```
python tools/preview.py
```

## Where things live

- **`options.txt`** — in the project directory, next to `run.py`. Nothing
  in it is sensitive by design, so it's fine to keep alongside the code.
- **`~/.config/pysence-standalone/`** — the one thing that *is* sensitive:
  the exchanged Discord token pair, encrypted at rest (Fernet). The key
  lives in your OS keyring if available, otherwise a chmod-600 file in the
  same directory. Delete `store.enc` (or
  `SecureStore().delete("discord_tokens")`) to force re-authorization.

## Editing OAuth scopes

`DEFAULT_SCOPES` in `pysence_standalone/discord/oauth.py` is a plain
**space**-separated string, e.g. `"rpc.activities.write openid"` — not
`+`-joined (that's only how it looks once URL-encoded in the browser
address bar). If you widen it, note that Discord will often grant extra
implied scopes on top of what you asked for (e.g. `sdk.social_layer`
alongside `rpc`) — `get_access_token()` accounts for this by checking that
your requested scopes are a *subset* of what's cached, not an exact match,
so this doesn't force a re-authorization every single run.

## `options.txt` — fully customizable, empty means off

Every content field is optional. If you leave it blank, **or** it renders
empty this cycle (e.g. it references an `api.*` field that hasn't
successfully fetched yet), that part simply isn't sent — no blank button,
no broken image, no empty state line.

```
script.user.id="YOUR_DISCORD_APPLICATION_ID"     # required

rpc.activity.name="pysence-standalone"            # falls back to script.dev.alias if blank
rpc.activity.details=""                           # optional
rpc.activity.state=""                              # optional
rpc.activity.type=0                                # 0 Playing, 1 Streaming, 2 Listening, 3 Watching, 5 Competing
rpc.activity.image=""                              # URL (auto-proxied) or a manually-uploaded Art Asset key
rpc.presence.status="online"                       # online / idle / dnd

rpc.button.one.text=""                             # button omitted unless BOTH text and url resolve
rpc.button.one.url=""
rpc.button.two.text=""
rpc.button.two.url=""

script.interval=60                                 # seconds between keep-alive PRESENCE_UPDATE resends
```

Full schema, defaults, and comments live in `pysence_standalone/core/options.py`
(`OPTION_SCHEMA`) — the source of truth, and what generates the template
file, so it can't drift out of sync with what the code reads.

### Placeholders

Any `"quoted"` content field supports:

| Token             | Meaning                                              |
| ------------------ | ----------------------------------------------------- |
| `{custom.<name>}` | from `placeholder.<name>="..."` lines                 |
| `{api.<name>}`    | from `api.<name>=standalone.http(...)` lines           |

```
placeholder.my.website="example.com"
rpc.button.two.url="https://{custom.my.website}"
```

Custom placeholders can reference other placeholders (including `{api.*}`
fields and other custom ones) in any order.

## `api.*` fields — the `standalone.http(...)` DSL

Define an HTTP call as a placeholder source:

```
api.weather=standalone.http("GET", "https://api.example.com/weather", ["q"="London","units"="metric"], "response.current.temp_text", interval=300)
rpc.activity.details="It's {api.weather} right now"
```

Arguments, positional in this order or as keywords (skip any you don't need):

| Arg        | Meaning                                                                                                   |
| ---------- | ----------------------------------------------------------------------------------------------------------- |
| `method`   | `"GET"` / `"POST"` / `"PUT"` / `"PATCH"` / `"DELETE"` — required                                             |
| `url`      | the request URL — required                                                                                   |
| `data`     | a `["key"="value", ...]` map, optional. GET → query params; everything else → JSON body                     |
| `response` | a dotted path into the response body, starting with `response`, e.g. `"response.data.text"` means "JSON body → `.data` → `.text`". Supports `[n]` list indexing, e.g. `"response.items[0].name"`. Omit to use the raw response text as-is. |
| `interval` | seconds between re-fetches. **Omit it and the field is fetched exactly once at startup and never refreshed** — this is the whole "no tracking unless added manually" behavior. Set it to opt that one field into its own independent polling loop. |

Keyword form works identically:

```
api.motto=standalone.http(method="GET", url="https://api.example.com/quote")
```

Each field gets its own background thread when `interval` is set, so a
slow or misbehaving API can't stall the others or the main Gateway
connection. Fields without `interval` are fetched once, synchronously,
before the very first presence update is sent.

`{api.<name>}` is available everywhere `{custom.<name>}` is — including
inside a `placeholder.*` definition.

### Human notifications

`human.discord.webhook` sends one short message whenever the *rendered*
activity content actually changes (e.g. because an `api.*` field on its
own interval fetched a new value) — never a batch, never on every
keep-alive resend.

```
human.discord.webhook=["https://discord.com/api/webhooks/..."]
human.message.changed="Presence updated: {rpc.activity.details}"
```

### Dev / logging

Same shape as the project this is based on: independent `script.dev.debug`
/ `.info` / `.warn` / `.error` toggles, plus an optional
`script.dev.discord.webhook` for the raw batched log firehose (separate
from the human-readable webhook above).

## Reconnect

Automatic, with exponential backoff and Discord's `RESUME` protocol when
possible:

```
script.reconnect.enabled=true
script.reconnect.base_delay=5
script.reconnect.max_delay=300
script.reconnect.max_attempts=0    # 0 = retry forever
```

## Running alongside ropysence

Running both scripts separately doesn't work — each opens its own
independent Discord Gateway session for your account, and Discord shows
whichever session sent the most recent presence update, not a merge of
both. This isn't a bug in either project; multiple activities only show
together when they're in the **same** `activities[]` array of **one**
presence update from **one** session.

`tools/run_combined.py` does exactly that: it imports both projects (they
no longer collide, now that this one is named `pysence_standalone` instead
of `src`), builds both activities every cycle, and sends them together
over a single shared connection.

```
python tools/run_combined.py --ropysence-dir /path/to/ropysence
```

Set up each project normally first (run each on its own once, fill in its
`options.txt`, authorize in the browser, and for ropysence enter your
Roblox cookie) — the combined runner reuses what's already cached in each
project's own `~/.config/*/` directory rather than asking for anything new.
Full details, including which project's settings govern the shared
connection, are in the script's docstring.

## Project structure

```
pysence-standalone/
├── run.py                    entry point
├── options.txt                created on first run, lives here (not sensitive)
├── pysence_standalone/        (deliberately NOT named `src` -- see note below)
│   ├── app.py                  orchestration
│   ├── presence_builder.py     options + placeholders -> Discord activity dict
│   ├── core/
│   │   ├── options.py            options.txt schema, parser, template generator
│   │   ├── http_api.py           standalone.http(...) DSL parser + fetch/poll manager
│   │   ├── secure_store.py       encrypted token storage (keyring / key file)
│   │   ├── templating.py         {token} substitution engine
│   │   ├── human_webhook.py      readable change-notification webhook
│   │   ├── logging_setup.py      INF/WRN/ERR/DBG config
│   │   └── webhook_logger.py     batched raw-log Discord webhook handler
│   └── discord/
│       ├── oauth.py               PKCE OAuth2 flow + cached token refresh
│       ├── gateway.py             Gateway connection, heartbeat, presence push
│       └── assets.py              external-assets image proxy
└── tools/
    └── preview.py               resolve + print the activity payload, no Discord calls
    └── run_combined.py          run alongside ropysence, one shared Gateway session
```

### A note on the package name

The source package here is `pysence_standalone`, not `src`. ropysence (the
project this is based on) also names its package `src` — if both projects
end up importable at once (e.g. both `pip install -e`'d, or both cloned
into directories on the same `PYTHONPATH`), Python's module cache only
keeps one `src`, and the other project silently runs the wrong code. Keep
it named `pysence_standalone` if you fork this further, for the same
reason.

## Security notes

- Keep your Discord Application ID as-is; it's not a secret, but the
  exchanged **access/refresh token pair is** — that's why it's the one
  thing kept encrypted outside the project directory.
- Any `api.*` field pointed at a service that needs an API key: if you
  bake the key into the URL or a `data` value, remember that
  `options.txt` is plaintext in the project directory by design (per the
  brief that started this project). If that's not acceptable for a given
  key, keep that key out of `options.txt` and inject it another way (e.g.
  an environment variable your own fork reads) rather than pasting it in.
  
