"""
Loads (or creates) options.txt -- a flat `dotted.key=value` config format.
Unlike ropysence, this file lives in the PROJECT DIRECTORY (next to run.py),
not under ~/.config -- nothing in it is sensitive by design (the one
sensitive thing, the exchanged Discord token pair, lives in the encrypted
store under ~/.config/pysence-standalone/ instead, see core/secure_store.py).

Three kinds of lines:
  1. schema keys       -- see OPTION_SCHEMA below, e.g. rpc.activity.name=...
  2. placeholder.<name>="..."             -- a static custom placeholder,
                                              referenced as {custom.<name>}
  3. api.<name>=standalone.http(...)      -- an HTTP-sourced field, see
                                              core/http_api.py for the full
                                              DSL. Referenced as {api.<name>}

FULLY CUSTOMIZABLE, EMPTY MEANS OFF: for every optional content field
(details, state, image, each button's text/url) an empty value after
template resolution means that part is simply not built/sent at all,
rather than sent as a blank string. This is checked at build time in
presence_builder.py, not here -- this module only loads and coerces raw
config values.

Lines starting with # (after stripping leading whitespace) and blank lines
are ignored. Unknown keys (that aren't placeholder.* or api.*) are warned
about, not fatal, so a stray typo doesn't crash the whole config.
"""

import json
from pathlib import Path

from src.core.logging_setup import get_logger
from src.core.http_api import parse_call as parse_http_call

log = get_logger("options")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPTIONS_FILE = PROJECT_ROOT / "options.txt"

CUSTOM_PLACEHOLDER_PREFIX = "placeholder."
API_FIELD_PREFIX = "api."

PLACEHOLDER_HELP = (
    "Supported placeholders (usable inside any \"quoted\" content field):\n"
    "  {custom.<name>}   from placeholder.<name>=\"...\" lines\n"
    "  {api.<name>}      from api.<name>=standalone.http(...) lines -- the\n"
    "                    field's most recently fetched value\n"
    "Missing/empty placeholders render as empty text, never an error. A\n"
    "button, the image, or a details/state line whose value resolves\n"
    "empty is left out entirely rather than shown blank.\n"
    "\n"
    "Custom placeholders may reference other placeholders (including\n"
    "{api.*} fields, and other custom ones) in any order:\n"
    "  placeholder.my.website=\"example.com\"\n"
    "  rpc.button.two.url=\"https://{custom.my.website}\"\n"
    "\n"
    "api.* fields run an HTTP request and expose the result as a\n"
    "placeholder. See the api.* section of this file for the full syntax."
)

_VALID_RPC_TYPES = {0, 1, 2, 3, 5}
_VALID_RPC_STATUSES = {"online", "idle", "dnd"}

# key -> (type, default, required, comment)
# type is one of "bool", "int", "str", "list"
OPTION_SCHEMA = {
    # --- Required ---
    "script.user.id": ("str", "", True, "Discord Application (Client) ID -- required"),

    # --- Activity content -- empty means that part is not built at all ---
    "rpc.activity.name": ("str", "pysence-standalone", False, "Top line of the activity. Falls back to script.dev.alias if left blank AND alias is also blank, since Discord requires SOME name."),
    "rpc.activity.details": ("str", "", False, "Second line. Leave blank to omit entirely."),
    "rpc.activity.state": ("str", "", False, "Third line. Leave blank to omit entirely."),
    "rpc.activity.type": ("int", 0, False, "Discord activity type: 0 Playing, 1 Streaming, 2 Listening, 3 Watching, 5 Competing"),
    "rpc.activity.image": ("str", "", False, "Large image: a URL (auto-proxied through Discord, zero setup) or a literal Rich Presence Art Asset key you've manually uploaded. Leave blank to omit the image entirely."),
    "rpc.presence.status": ("str", "online", False, "Discord status shown alongside the activity: online / idle / dnd (invisible is not supported)"),

    # --- Buttons (Discord allows max 2). A button is only shown if BOTH its
    #     text and url are non-empty and fully resolve (no missing token). ---
    "rpc.button.one.text": ("str", "", False, "Label for the first button. Leave blank (or leave the url blank) to omit this button."),
    "rpc.button.one.url": ("str", "", False, "URL for the first button."),
    "rpc.button.two.text": ("str", "", False, "Label for the second button."),
    "rpc.button.two.url": ("str", "", False, "URL for the second button."),

    # --- Behavior ---
    "script.interval": ("int", 60, False, "Seconds between PRESENCE_UPDATE resends over the Gateway. This is just a keep-fresh resend, not a data poll -- with no api.* fields set to an interval, nothing changes between resends anyway, so this can be left fairly high. Individual api.* fields refresh on their OWN interval regardless of this value."),
    "script.localhost.port": ("int", 8969, False, "Local port used to catch the OAuth redirect"),

    # --- Reconnect ---
    "script.reconnect.enabled": ("bool", True, False, "Automatically reconnect (with RESUME when possible) if the Gateway connection drops"),
    "script.reconnect.base_delay": ("int", 5, False, "Seconds to wait before the first reconnect attempt; doubles after each further failure"),
    "script.reconnect.max_delay": ("int", 300, False, "Cap on the backoff delay between reconnect attempts, in seconds"),
    "script.reconnect.max_attempts": ("int", 0, False, "Give up after this many consecutive failed attempts; 0 means retry forever"),

    # --- Human notifications: one short message whenever the built
    #     activity text actually changes (e.g. an api.* field's value
    #     changed on its own poll). Empty webhook list = feature is off. ---
    "human.discord.webhook": ("list", [], False, "Discord webhook URL(s) for readable change notifications, e.g. [\"url1\",\"url2\"]. Sent only when the rendered activity text actually changes. Leave empty to disable."),
    "human.message.changed": ("str", "Presence updated: {rpc.activity.details}", False, "Template for the change notification."),

    # --- Dev / logging ---
    "script.dev.debug": ("bool", False, False, "Show DBG-level logs"),
    "script.dev.info": ("bool", False, False, "Show INF-level logs"),
    "script.dev.warn": ("bool", True, False, "Show WRN-level logs"),
    "script.dev.error": ("bool", True, False, "Show ERR-level logs"),
    "script.dev.discord.webhook": ("list", [], False, "Discord webhook URL(s) for raw batched log output. This is the technical firehose -- see human.discord.webhook above for a readable alternative."),
    "script.dev.discord.webhook.interval": ("int", 30, False, "Seconds between webhook log flushes"),
    "script.dev.alias": ("str", "pysence-standalone", False, "Used as the Gateway client name, webhook username, and fallback activity name if rpc.activity.name is blank"),
}

_TYPE_ORDER = ["Required", "Activity", "Buttons", "Behavior", "Reconnect", "Human notifications", "Dev / logging"]
_SECTION_OF = {
    "script.user.id": "Required",
    "rpc.activity.name": "Activity", "rpc.activity.details": "Activity", "rpc.activity.state": "Activity",
    "rpc.activity.type": "Activity", "rpc.activity.image": "Activity", "rpc.presence.status": "Activity",
    "rpc.button.one.text": "Buttons", "rpc.button.one.url": "Buttons",
    "rpc.button.two.text": "Buttons", "rpc.button.two.url": "Buttons",
    "script.interval": "Behavior", "script.localhost.port": "Behavior",
    "script.reconnect.enabled": "Reconnect", "script.reconnect.base_delay": "Reconnect",
    "script.reconnect.max_delay": "Reconnect", "script.reconnect.max_attempts": "Reconnect",
    "human.discord.webhook": "Human notifications", "human.message.changed": "Human notifications",
    "script.dev.debug": "Dev / logging", "script.dev.info": "Dev / logging",
    "script.dev.warn": "Dev / logging", "script.dev.error": "Dev / logging",
    "script.dev.discord.webhook": "Dev / logging", "script.dev.discord.webhook.interval": "Dev / logging",
    "script.dev.alias": "Dev / logging",
}


def render_default_template() -> str:
    lines = [
        "# pysence-standalone options -- flat key=value config.",
        "# Nothing in this file is sensitive; it lives in the project directory on purpose.",
        "# The Discord token pair exchanged at runtime lives encrypted in ~/.config/pysence-standalone/ instead.",
        "#",
        "# " + PLACEHOLDER_HELP.replace("\n", "\n# "),
        "",
    ]
    by_section = {s: [] for s in _TYPE_ORDER}
    for key, (typ, default, required, comment) in OPTION_SCHEMA.items():
        by_section[_SECTION_OF[key]].append((key, typ, default, required, comment))

    for section in _TYPE_ORDER:
        entries = by_section[section]
        if not entries:
            continue
        lines.append(f"# --- {section} {'(required)' if section == 'Required' else ''} ---".rstrip())
        for key, typ, default, required, comment in entries:
            if comment:
                lines.append(f"# {comment}")
            if typ == "str":
                lines.append(f'{key}="{default}"')
            elif typ == "list":
                lines.append(f"{key}={json.dumps(default)}")
            elif typ == "bool":
                lines.append(f"{key}={'true' if default else 'false'}")
            else:
                lines.append(f"{key}={default}")
        lines.append("")

    lines.append("# --- Custom static placeholders -- as many as you want, named anything ---")
    lines.append('# placeholder.my.website="example.com"')
    lines.append('# rpc.button.two.url="https://{custom.my.website}"')
    lines.append("")
    lines.append("# --- api.* HTTP-sourced fields -- see core/http_api.py for the full syntax ---")
    lines.append("# Fetched once at startup by default. Add interval=<seconds> to poll repeatedly.")
    lines.append('# api.weather=standalone.http("GET", "https://api.example.com/weather", ["q"="London"], "response.current.temp_text", interval=300)')
    lines.append("# rpc.activity.details=\"It's {api.weather} right now\"")
    lines.append("")
    return "\n".join(lines)


def _strip_quotes(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    return raw


def _coerce(key: str, raw: str, expected_type: str):
    raw = raw.strip()
    if expected_type == "bool":
        low = _strip_quotes(raw).lower()
        if low not in ("true", "false"):
            raise ValueError(f"'{key}' expects true/false, got: {raw}")
        return low == "true"
    if expected_type == "int":
        try:
            return int(_strip_quotes(raw))
        except ValueError:
            raise ValueError(f"'{key}' expects an integer, got: {raw}")
    if expected_type == "list":
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError(f"'{key}' expects a JSON array like [\"url1\",\"url2\"], got: {raw}")
            if not isinstance(parsed, list):
                raise ValueError(f"'{key}' expects a JSON array like [\"url1\",\"url2\"], got: {raw}")
            return [str(v).strip() for v in parsed if str(v).strip()]
        single = _strip_quotes(raw)
        return [single] if single else []
    return _strip_quotes(raw)


def _parse_lines(text: str) -> dict:
    raw = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            log.warning("options.txt line %d looks malformed (no '='), ignoring: %s", lineno, stripped)
            continue
        key, _, value = stripped.partition("=")
        raw[key.strip()] = value
    return raw


def load_options() -> dict:
    if not OPTIONS_FILE.exists():
        log.info("no options.txt found, writing a documented default to %s", OPTIONS_FILE)
        OPTIONS_FILE.write_text(render_default_template())
        log.error("options.txt was just created -- set script.user.id to your Discord Application ID, then rerun")
        raise SystemExit(1)

    raw = _parse_lines(OPTIONS_FILE.read_text())

    custom_placeholders = {}
    api_fields = {}
    for key in list(raw.keys()):
        if key.startswith(CUSTOM_PLACEHOLDER_PREFIX):
            name = key[len(CUSTOM_PLACEHOLDER_PREFIX):]
            if not name:
                log.warning("options.txt has a bare 'placeholder.' with no name after it -- ignoring")
                del raw[key]
                continue
            custom_placeholders[name] = _strip_quotes(raw.pop(key))
        elif key.startswith(API_FIELD_PREFIX):
            name = key[len(API_FIELD_PREFIX):]
            value = raw.pop(key)
            if not name:
                log.warning("options.txt has a bare 'api.' with no name after it -- ignoring")
                continue
            try:
                api_fields[name] = parse_http_call(value)
            except ValueError as e:
                log.error("options.txt: api.%s -- %s -- this field will be skipped", name, e)

    for key in raw:
        if key not in OPTION_SCHEMA:
            log.warning("unknown option '%s' in options.txt -- ignoring", key)

    resolved = {}
    missing_required = []
    for key, (typ, default, required, _comment) in OPTION_SCHEMA.items():
        if key in raw:
            try:
                resolved[key] = _coerce(key, raw[key], typ)
            except ValueError as e:
                log.error("options.txt: %s -- using default instead", e)
                resolved[key] = default
        else:
            resolved[key] = default

        if required and (resolved[key] == "" or resolved[key] is None):
            missing_required.append(key)

    if missing_required:
        for key in missing_required:
            log.error("required option '%s' is not set in options.txt", key)
        raise SystemExit(1)

    if resolved["rpc.presence.status"].lower() not in _VALID_RPC_STATUSES:
        log.error(
            "rpc.presence.status='%s' is not valid -- must be one of %s",
            resolved["rpc.presence.status"], sorted(_VALID_RPC_STATUSES),
        )
        raise SystemExit(1)
    resolved["rpc.presence.status"] = resolved["rpc.presence.status"].lower()

    if resolved["rpc.activity.type"] not in _VALID_RPC_TYPES:
        log.error("rpc.activity.type=%s is not valid -- must be one of %s", resolved["rpc.activity.type"], sorted(_VALID_RPC_TYPES))
        raise SystemExit(1)

    img_default = resolved["rpc.activity.image"]
    if img_default and not img_default.startswith(("http://", "https://")):
        log.warning(
            "rpc.activity.image='%s' is not a URL, so it's being treated as a literal Rich "
            "Presence Art Asset key -- this ONLY works if you've manually uploaded an image under "
            "that exact name in your Discord app's dev portal already. Referencing a nonexistent "
            "asset key appears to make Discord silently drop the entire activity update, not just "
            "the image. Safer options: point it at a URL (auto-proxied, no setup), or clear it to "
            "omit the image.", img_default,
        )

    if custom_placeholders:
        log.debug("parsed %d custom placeholder(s): %s", len(custom_placeholders), sorted(custom_placeholders))
    if api_fields:
        log.debug("parsed %d api.* field(s): %s", len(api_fields), sorted(api_fields))
    resolved["_custom_placeholders"] = custom_placeholders
    resolved["_api_fields"] = api_fields

    log.debug("options.txt loaded (%d keys, %d custom placeholder(s), %d api field(s))",
              len(resolved), len(custom_placeholders), len(api_fields))
    return resolved
