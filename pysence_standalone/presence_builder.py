"""
Turns options.txt + the current {custom.*}/{api.*} placeholder values into
a Discord Rich Presence activity dict. No external service is polled here
by default -- see core/http_api.py for the only source of "live" data
(api.* fields), which is entirely opt-in per field.

EMPTY MEANS OFF: rpc.activity.details, rpc.activity.state, rpc.activity.image,
and each button's text/url are all optional. If a field is blank in
options.txt, OR it renders empty/partially-missing this cycle (e.g. it
references an {api.*} field that hasn't successfully fetched yet), that
part is simply left out of the activity payload rather than sent blank.
rpc.activity.name is the one exception -- Discord requires an activity to
have a name, so an empty result falls back to script.dev.alias.

Buttons: exactly two generic slots (Discord's own limit), each just a
text+url template pair. A button is included only if BOTH its text and url
are non-empty and fully resolve (no missing placeholder).

The elapsed-time counter (Discord's "X seconds elapsed" under the activity)
only resets when the rendered content actually changes, not on every
keep-alive resend -- so it accurately reflects how long the CURRENT status
has been showing, not how long the process has been running. The human
webhook notification (if configured) fires on that same "content actually
changed" transition, so it's genuinely low-volume rather than one message
per resend.
"""

import json
import time

from pysence_standalone.core.logging_setup import get_logger
from pysence_standalone.core.templating import render, render_track_missing, resolve_custom_placeholders
from pysence_standalone.discord.assets import proxy_image_urls

log = get_logger("presence_builder")


class PresenceBuilder:
    def __init__(self, options: dict, api_manager, get_access_token_fn, client_id: str, human_notifier=None):
        self.opt = options
        self.api_manager = api_manager
        self.get_access_token_fn = get_access_token_fn  # zero-arg callable, always returns a currently-valid token
        self.client_id = client_id
        self.human_notifier = human_notifier
        self._last_signature = None
        self._start_ms = int(time.time() * 1000)

    def _context(self) -> dict:
        context = self.api_manager.context()
        context.update(resolve_custom_placeholders(self.opt.get("_custom_placeholders", {}), context))
        return context

    def _build_button(self, number: str, context: dict):
        """number is 'one' or 'two'. Returns {"label", "url"} or None if
        either side is blank or references a placeholder that isn't
        available right now."""
        text_template = self.opt[f"rpc.button.{number}.text"]
        url_template = self.opt[f"rpc.button.{number}.url"]
        if not text_template or not url_template:
            return None
        label, label_missing = render_track_missing(text_template, context)
        url, url_missing = render_track_missing(url_template, context)
        if not label or not url or label_missing or url_missing:
            log.debug("button '%s' skipped this cycle (missing placeholder or empty result)", number)
            return None
        return {"label": label, "url": url}

    def build(self):
        """Returns a Discord activity dict. Never returns None -- with no
        api.* fields configured there's nothing that can transiently fail
        the way a live API call could, so every cycle produces a valid
        (if minimal) activity."""
        context = self._context()

        name = render(self.opt["rpc.activity.name"], context)
        if not name:
            name = self.opt["script.dev.alias"] or "pysence-standalone"

        details, details_missing = render_track_missing(self.opt["rpc.activity.details"], context)
        if details_missing:
            details = ""
        state, state_missing = render_track_missing(self.opt["rpc.activity.state"], context)
        if state_missing:
            state = ""

        image_template = self.opt["rpc.activity.image"]
        large_image = None
        large_image_url_to_proxy = None
        if image_template:
            image_rendered, image_missing = render_track_missing(image_template, context)
            if image_rendered and not image_missing:
                if image_rendered.startswith(("http://", "https://")):
                    large_image_url_to_proxy = image_rendered
                else:
                    large_image = image_rendered  # literal Rich Presence Art Asset key

        buttons = []
        for number in ("one", "two"):
            b = self._build_button(number, context)
            if b:
                buttons.append(b)

        if large_image_url_to_proxy:
            access_token = self.get_access_token_fn()
            proxied = proxy_image_urls(access_token, self.client_id, [large_image_url_to_proxy])
            large_image = proxied.get(large_image_url_to_proxy)
            if not large_image:
                log.warning("image proxy failed for %s -- omitting image this cycle", large_image_url_to_proxy)

        # Only reset the elapsed-time counter (and fire the human
        # notification) when the rendered content genuinely changed since
        # the last cycle -- not on every keep-alive resend.
        signature = (name, details, state, tuple(b["label"] for b in buttons), large_image)
        content_changed = self._last_signature != signature
        if content_changed:
            if self._last_signature is not None:
                log.info("presence content changed -- resetting elapsed-time counter")
                if self.human_notifier and self.human_notifier.enabled:
                    msg = render(self.opt["human.message.changed"], {
                        **context,
                        "rpc.activity.name": name, "rpc.activity.details": details, "rpc.activity.state": state,
                    })
                    self.human_notifier.notify(msg)
            self._last_signature = signature
            self._start_ms = int(time.time() * 1000)

        activity = {
            "name": name,
            "type": self.opt["rpc.activity.type"],
            "application_id": self.client_id,
            "timestamps": {"start": self._start_ms},
        }
        if details:
            activity["details"] = details
        if state:
            activity["state"] = state
        if large_image:
            activity["assets"] = {"large_image": large_image, "large_text": state or details or name}
        if buttons:
            activity["buttons"] = [b["label"] for b in buttons]
            activity["metadata"] = {"button_urls": [b["url"] for b in buttons]}

        log.info("build_activity: name='%s' details='%s' state='%s' buttons=%s", name, details, state, [b["label"] for b in buttons])
        log.debug("full activity payload: %s", json.dumps(activity))
        return activity
