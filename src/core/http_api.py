"""
The `standalone.http(...)` mini-DSL that can appear as the value of any
`api.<name>=` line in options.txt, e.g.:

    api.weather=standalone.http("GET", "https://api.example.com/weather",
        ["q"="London","units"="metric"], "response.current.temp_text", interval=300)

    api.motto=standalone.http(method="GET", url="https://api.example.com/quote")

Positional order is (method, url, data, response); everything is also
available as a keyword so you can skip arguments freely:
  method    "GET" / "POST" / "PUT" / "PATCH" / "DELETE" -- required
  url       the request URL -- required
  data      a ["key"="value", ...] map -- optional (default: none). For GET
            these become query-string parameters; for everything else, a
            JSON request body.
  response  a dotted path INTO the response body, always starting with the
            literal word "response" -- e.g. "response.data.text" means
            "take the JSON body, go into .data, then .text". Optional: if
            omitted, the raw response text is used as-is. Supports a
            trailing [n] on a path segment for list indexing, e.g.
            "response.items[0].name".
  interval  seconds between re-fetches. Optional: if you don't set it, the
            field is fetched exactly ONCE at startup and never refreshed --
            this is the mechanism behind "no tracking unless added
            manually" mentioned in the project brief. Set it to opt a
            specific field into its own independent polling loop.

Each `api.<name>` field's resolved value is exposed to every templated
content field in options.txt as `{api.<name>}`, same placeholder mechanism
as `{custom.<name>}`.

This is deliberately NOT JSON (the ["k"="v"] map uses `=`, not `:`, to stay
visually consistent with the rest of options.txt's `key=value` style), so
it needs its own small parser rather than json.loads. The parser only
understands this one shape -- it is not a general expression language.
"""

import json
import re
import threading
import time

import requests

from src.core.logging_setup import get_logger

log = get_logger("http_api")

CALL_PREFIX = "standalone.http("
_KWARG_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", re.S)
_PATH_SEGMENT_RE = re.compile(r"^([A-Za-z0-9_]+)(?:\[(\d+)\])?$")
_VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _split_top_level(s: str, sep: str = ",") -> list:
    """Splits on `sep`, but only at nesting depth 0 -- content inside ()/[]
    or "..."/'...' is never split on, so a nested ["k"="v","k2"="v2"] map
    stays intact as a single top-level token."""
    parts, current = [], []
    depth = 0
    in_quotes = False
    quote_char = ""
    for ch in s:
        if in_quotes:
            current.append(ch)
            if ch == quote_char:
                in_quotes = False
            continue
        if ch in ("\"", "'"):
            in_quotes = True
            quote_char = ch
            current.append(ch)
            continue
        if ch in "([":
            depth += 1
            current.append(ch)
            continue
        if ch in ")]":
            depth -= 1
            current.append(ch)
            continue
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("\"", "'"):
        return s[1:-1]
    return s


def _parse_map(s: str) -> dict:
    s = s.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ValueError(f"expected a [\"k\"=\"v\"] map, got: {s}")
    inner = s[1:-1].strip()
    if not inner:
        return {}
    result = {}
    for piece in _split_top_level(inner, ","):
        if "=" not in piece:
            raise ValueError(f"map entry missing '=': {piece}")
        k, _, v = piece.partition("=")
        result[_strip_quotes(k)] = _strip_quotes(v)
    return result


def _parse_scalar(token: str):
    token = token.strip()
    if token.startswith("["):
        return _parse_map(token)
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("\"", "'"):
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        return token  # bareword, e.g. an unquoted GET


def parse_call(raw: str) -> dict:
    """Parses one `api.<name>=standalone.http(...)` value. Raises ValueError
    with a human-readable message on anything malformed -- the caller
    (options.py) catches this and drops just that one field rather than
    crashing the whole config load."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("\"", "'"):
        raw = raw[1:-1].strip()  # tolerate an accidentally fully-quoted value
    if not raw.startswith(CALL_PREFIX) or not raw.endswith(")"):
        raise ValueError(f"expected standalone.http(...), got: {raw}")

    inner = raw[len(CALL_PREFIX):-1]
    tokens = _split_top_level(inner, ",")

    positional, kwargs = [], {}
    for tok in tokens:
        m = _KWARG_RE.match(tok)
        if m and not tok.lstrip().startswith(("\"", "'", "[")):
            kwargs[m.group(1)] = _parse_scalar(m.group(2))
        else:
            positional.append(_parse_scalar(tok))

    remaining = list(positional)
    method = kwargs.get("method") or (remaining.pop(0) if remaining else None)
    url = kwargs.get("url") or (remaining.pop(0) if remaining else None)
    data = kwargs.get("data") if "data" in kwargs else (remaining.pop(0) if remaining else None)
    response = kwargs.get("response") if "response" in kwargs else (remaining.pop(0) if remaining else None)
    interval = kwargs.get("interval")

    if not method or not isinstance(method, str):
        raise ValueError("missing method, e.g. \"GET\"")
    method = method.strip().upper()
    if method not in _VALID_METHODS:
        raise ValueError(f"method must be one of {sorted(_VALID_METHODS)}, got: {method}")

    if not url or not isinstance(url, str):
        raise ValueError("missing url")

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"data must be a [\"k\"=\"v\"] map, got: {data!r}")

    if response is not None and not isinstance(response, str):
        raise ValueError("response path must be a quoted string, e.g. \"response.data.text\"")

    if interval is not None:
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            raise ValueError(f"interval must be an integer, got: {interval!r}")
        if interval <= 0:
            raise ValueError("interval must be a positive number of seconds")

    return {"method": method, "url": url, "data": data, "response": response, "interval": interval}


def resolve_response_path(path, json_body, raw_text: str) -> str:
    """path is a "response.a.b[0].c" string (or None). Missing/unreachable
    segments resolve to "" rather than raising, matching the templating
    engine's existing convention of blanking out missing placeholders."""
    if path is None:
        return raw_text
    parts = path.split(".")
    if parts and parts[0] == "response":
        parts = parts[1:]
    if not parts:
        if isinstance(json_body, (dict, list)):
            return json.dumps(json_body)
        return raw_text

    current = json_body
    for part in parts:
        m = _PATH_SEGMENT_RE.match(part)
        if not m or current is None:
            return ""
        key, idx = m.group(1), m.group(2)
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return ""
        if idx is not None:
            try:
                current = current[int(idx)]
            except (IndexError, TypeError, KeyError):
                return ""
    if current is None:
        return ""
    if isinstance(current, (dict, list)):
        return json.dumps(current)
    return str(current)


class HttpApiField:
    def __init__(self, name: str, config: dict):
        self.name = name
        self.method = config["method"]
        self.url = config["url"]
        self.data = config["data"]
        self.response_path = config["response"]
        self.interval = config["interval"]
        self._value = ""
        self._lock = threading.Lock()

    def fetch_once(self):
        try:
            if self.method == "GET":
                resp = requests.get(self.url, params=self.data or None, timeout=10)
            else:
                resp = requests.request(self.method, self.url, json=self.data or None, timeout=10)
        except requests.RequestException as e:
            log.warning("api.%s: request to %s failed: %s", self.name, self.url, e)
            return

        if resp.status_code >= 400:
            log.warning("api.%s: HTTP %d from %s", self.name, resp.status_code, self.url)

        try:
            json_body = resp.json()
        except ValueError:
            json_body = None

        value = resolve_response_path(self.response_path, json_body, resp.text)
        with self._lock:
            self._value = value
        log.debug("api.%s resolved -> %r", self.name, value[:200])

    def get(self) -> str:
        with self._lock:
            return self._value


class HttpApiManager:
    """Owns every api.* field parsed from options.txt. Fields without an
    `interval` are fetched exactly once, synchronously, during start() --
    so the very first presence build already has real data, and nothing
    ever polls them again. Fields WITH an interval each get their own
    daemon thread with an independent loop, so a slow/misbehaving API
    can't stall the others or the main Gateway loop."""

    def __init__(self, field_configs: dict):
        self.fields = {name: HttpApiField(name, cfg) for name, cfg in field_configs.items()}

    def start(self):
        if not self.fields:
            return
        log.info("fetching %d api.* field(s) for the first time", len(self.fields))
        for field in self.fields.values():
            field.fetch_once()
        for field in self.fields.values():
            if field.interval:
                threading.Thread(
                    target=self._poll_loop, args=(field,), daemon=True, name=f"api-{field.name}",
                ).start()
                log.info("api.%s will re-fetch every %ss", field.name, field.interval)
            else:
                log.info("api.%s fetched once, will not be refreshed (no interval set)", field.name)

    def _poll_loop(self, field: HttpApiField):
        while True:
            time.sleep(field.interval)
            field.fetch_once()

    def context(self) -> dict:
        return {f"api.{name}": field.get() for name, field in self.fields.items()}
