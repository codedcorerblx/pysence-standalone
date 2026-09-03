"""
{token} templating for options.txt content fields. Handles two flavors of
user-defined tokens, both resolved into a flat context dict every poll:
  {custom.<name>}  from placeholder.<name>="..." lines (static-ish; may
                    reference other placeholders)
  {api.<name>}     from api.<name>=standalone.http(...) lines (the field's
                    most recently fetched value -- see core/http_api.py)

Deliberately not using str.format(): dotted tokens like {api.weather}
trigger attribute-access semantics in str.format, not dict-key lookup,
which fights a flat dotted-key context dict. A small regex substitution is
simpler.
"""

import re

from src.core.logging_setup import get_logger

log = get_logger("templating")

_TOKEN_RE = re.compile(r"\{([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*)\}")


def render(template: str, context: dict) -> str:
    """context is flat, keyed by dotted strings, e.g. {"api.weather": "72F"}.
    Missing tokens (or explicitly None values) render as empty string --
    expected/normal, not an error."""
    return render_track_missing(template, context)[0]


def render_track_missing(template: str, context: dict) -> tuple:
    """Same as render(), but also returns whether any token in the template
    was missing/None. Used to decide whether a fully user-configurable
    field (a button, the image, a details/state line) should be built at
    all -- if a placeholder it depends on isn't available, that part is
    omitted rather than shown broken."""
    if not template:
        return "", False

    missing = [False]

    def _sub(match: re.Match) -> str:
        token = match.group(1)
        value = context.get(token)
        if value is None:
            missing[0] = True
            log.debug("template token '{%s}' not present in this context, rendering empty", token)
            return ""
        return str(value)

    return _TOKEN_RE.sub(_sub, template), missing[0]


def resolve_custom_placeholders(raw_templates: dict, base_context: dict, max_passes: int = 5) -> dict:
    """raw_templates: {name: raw_template_string} as parsed from
    `placeholder.<name>="..."` lines in options.txt (name has NOT been
    prefixed with "custom." yet). Returns {"custom.<name>": resolved_value}
    ready to merge into a render context.

    Custom placeholder values may themselves reference other placeholders
    (built-in {api.*} fields, which can change between poll cycles, or
    other custom ones) in any declaration order. Resolved with a few
    fixed-point passes so forward and backward references both work; a
    value still containing an unresolved token after max_passes is logged
    (likely a circular reference, or it points at something that doesn't
    exist)."""
    if not raw_templates:
        return {}

    working = dict(base_context)
    for name in raw_templates:
        working.setdefault(f"custom.{name}", None)

    for _pass in range(max_passes):
        changed = False
        for name, template in raw_templates.items():
            key = f"custom.{name}"
            new_value = render(template, working)
            if working.get(key) != new_value:
                working[key] = new_value
                changed = True
        if not changed:
            break

    result = {}
    for name in raw_templates:
        key = f"custom.{name}"
        value = working.get(key, "")
        if _TOKEN_RE.search(value or ""):
            log.warning(
                "custom placeholder '{%s}' still contains an unresolved token after %d pass(es) "
                "-- likely a circular reference, or it points at something that doesn't exist. Got: %r",
                key, max_passes, value,
            )
        result[key] = value
    return result
