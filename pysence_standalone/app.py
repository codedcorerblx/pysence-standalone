"""
Wires everything together: options.txt -> api.* HTTP fields -> Discord
OAuth -> Gateway loop. Called from run.py at the repo root.

Unlike ropysence, there is no external account/session to monitor by
default -- this build only "tracks" something if you explicitly define an
api.<name>=standalone.http(..., interval=N) field in options.txt. With no
such fields, or none with an interval, the process just authorizes once,
sends one PRESENCE_UPDATE, and keeps the Gateway socket alive.
"""

import asyncio
import sys

from pysence_standalone.core.logging_setup import setup_logging, apply_options, get_logger
from pysence_standalone.core.secure_store import SecureStore
from pysence_standalone.core.options import load_options
from pysence_standalone.core.http_api import HttpApiManager
from pysence_standalone.core.human_webhook import HumanWebhookNotifier
from pysence_standalone.discord.oauth import get_access_token, DEFAULT_SCOPES
from pysence_standalone.discord.gateway import run_gateway_with_reconnect
from pysence_standalone.presence_builder import PresenceBuilder

setup_logging()
log = get_logger("app")


def main():
    log.info("starting pysence-standalone")
    options = load_options()
    apply_options(options)

    store = SecureStore()
    client_id = options["script.user.id"]

    api_manager = HttpApiManager(options["_api_fields"])
    api_manager.start()  # fetch-once fields resolve here; interval fields keep polling in the background

    def get_token():
        # Cheap to call repeatedly -- returns the cached token when still
        # valid, only does real work (refresh or full re-auth) when needed.
        return get_access_token(client_id, DEFAULT_SCOPES, options["script.localhost.port"], store)

    human_notifier = HumanWebhookNotifier(
        webhook_urls=options["human.discord.webhook"],
        alias=options["script.dev.alias"],
    )

    builder = PresenceBuilder(
        options=options,
        api_manager=api_manager,
        get_access_token_fn=get_token,
        client_id=client_id,
        human_notifier=human_notifier,
    )

    poll_interval = options["script.interval"]
    log.info(
        "entering main loop (resend_interval=%ss, status=%s, reconnect=%s, api_fields=%d)",
        poll_interval, options["rpc.presence.status"], options["script.reconnect.enabled"], len(api_manager.fields),
    )

    try:
        asyncio.run(run_gateway_with_reconnect(
            get_access_token_fn=get_token,
            build_activity_fn=builder.build,
            poll_interval=poll_interval,
            status=options["rpc.presence.status"],
            alias=options["script.dev.alias"],
            reconnect_enabled=options["script.reconnect.enabled"],
            base_delay=options["script.reconnect.base_delay"],
            max_delay=options["script.reconnect.max_delay"],
            max_attempts=options["script.reconnect.max_attempts"],
        ))
    except KeyboardInterrupt:
        log.info("interrupted by user, shutting down")
    except Exception as e:
        log.error("fatal error: %s", e)
        sys.exit(1)
    finally:
        human_notifier.close()
