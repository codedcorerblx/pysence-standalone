#!/usr/bin/env python3
"""
Runs pysence-standalone's custom activity AND ropysence's Roblox-driven
activity together, from ONE Discord Gateway connection, so both show up on
your profile at once instead of the most-recently-started one overwriting
the other.

WHY THIS IS NECESSARY: each script, run separately, opens its own
independent Gateway "session" for your account and sends its own
PRESENCE_UPDATE. Discord's presence system displays whichever session sent
the most recent update -- it does not merge activities across two
independent sessions. Multiple activities showing together only happens
when they're packed into the SAME activities[] array of ONE presence
update sent from ONE session. This script does exactly that.

SETUP (do this once, for each project, the normal way -- this script
doesn't ask for anything new, it just reuses what's already cached):
  1. cd ropysence && python run.py
     Fill in options.txt, authorize in the browser once, paste your Roblox
     cookie once. Ctrl+C once you see it connect and send a presence update.
  2. cd pysence-standalone && python run.py
     Fill in options.txt, authorize in the browser once. Ctrl+C once
     connected.
Both now have their own cached OAuth tokens (and ropysence its cached
Roblox cookie) in their own separate ~/.config/*/ directories -- fully
isolated from each other, this script just imports and reuses both.

USAGE:
    python tools/run_combined.py [--ropysence-dir PATH]

    PATH defaults to a `ropysence` directory next to this project's parent
    (../ropysence), or set the ROPYSENCE_DIR environment variable.

NOTES / CAVEATS:
  - The shared Gateway connection's identity (the IDENTIFY token) and its
    status/interval/reconnect settings come from pysence-standalone's
    options.txt -- ropysence's script.interval/rpc.state/reconnect.* are
    NOT used here, only its Roblox monitoring + activity-building logic is.
  - Likewise, script.dev.* verbosity toggles are taken from
    pysence-standalone's options.txt for BOTH apps' log output in this
    combined run.
  - Each app's OWN OAuth token is still used when IT proxies an external
    image URL (rpc.activity.image / the Roblox game icon) -- that call is
    tied to the requesting application, so ropysence's image goes through
    ropysence's token/app id, and pysence-standalone's through its own,
    even though both ride on the one shared connection.
  - If one side's build() fails for a cycle (e.g. Roblox is rate-limiting,
    or an api.* field's HTTP call fails), the other side's activity still
    sends -- a bad cycle on one side doesn't blank out the other.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent


def _parse_args():
    parser = argparse.ArgumentParser(description="Run pysence-standalone and ropysence together, one shared Gateway session.")
    parser.add_argument(
        "--ropysence-dir",
        default=os.environ.get("ROPYSENCE_DIR", str(PROJECT_ROOT.parent / "ropysence")),
        help="Path to a ropysence checkout (default: ../ropysence next to this project, or $ROPYSENCE_DIR)",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    ropysence_dir = Path(args.ropysence_dir).resolve()
    if not (ropysence_dir / "src" / "ropysence" / "app.py").exists():
        print(f"Could not find a ropysence checkout at {ropysence_dir} (expected src/ropysence/app.py there).")
        print("Pass --ropysence-dir /path/to/ropysence, or set the ROPYSENCE_DIR environment variable.")
        sys.exit(1)

    # ropysence resolves as `src.ropysence.*`; this project resolves as
    # `pysence_standalone.*` -- they don't collide, so both are importable
    # in the same process. This project's root goes first so it always wins
    # if anything is ever named the same on both sides by accident.
    sys.path.insert(0, str(PROJECT_ROOT))
    sys.path.append(str(ropysence_dir))

    # ropysence's `src.ropysence.core.logging_setup` is a physically separate
    # module from our `pysence_standalone.core.logging_setup`, with its own
    # _CONFIGURED flag -- imported normally, it would attach a SECOND
    # handler to the shared root logger and every line would print twice.
    # Short-circuit its setup before anything triggers it for real.
    import src.ropysence.core.logging_setup as robx_logging_setup
    robx_logging_setup._CONFIGURED = True

    from pysence_standalone.core.logging_setup import setup_logging, apply_options, get_logger
    setup_logging()
    log = get_logger("run_combined")

    from pysence_standalone.core.options import load_options as standalone_load_options
    from pysence_standalone.core.secure_store import SecureStore as StandaloneStore
    from pysence_standalone.core.http_api import HttpApiManager
    from pysence_standalone.discord.oauth import get_access_token as standalone_get_token, DEFAULT_SCOPES as STANDALONE_SCOPES
    from pysence_standalone.discord.gateway import run_gateway_with_reconnect
    from pysence_standalone.presence_builder import PresenceBuilder as StandalonePresenceBuilder

    from src.ropysence.core.options import load_options as robx_load_options
    from src.ropysence.core.secure_store import SecureStore as RobxStore
    from src.ropysence.discord.oauth import get_access_token as robx_get_token, DEFAULT_SCOPES as ROBX_SCOPES
    from src.ropysence.app import get_roblox_client
    from src.ropysence.roblox.presence_builder import PresenceBuilder as RobloxPresenceBuilder

    log.info("loading pysence-standalone options from %s", PROJECT_ROOT)
    standalone_options = standalone_load_options()
    apply_options(standalone_options)  # single call -- see NOTES above on why ropysence's own apply_options is skipped
    standalone_store = StandaloneStore()
    standalone_client_id = standalone_options["script.user.id"]

    def standalone_token():
        return standalone_get_token(standalone_client_id, STANDALONE_SCOPES, standalone_options["script.localhost.port"], standalone_store)

    api_manager = HttpApiManager(standalone_options["_api_fields"])
    api_manager.start()

    standalone_builder = StandalonePresenceBuilder(
        options=standalone_options,
        api_manager=api_manager,
        get_access_token_fn=standalone_token,
        client_id=standalone_client_id,
    )

    log.info("loading ropysence options + Roblox session from %s", ropysence_dir)
    robx_options = robx_load_options()
    robx_store = RobxStore()
    robx_client_id = robx_options["script.user.id"]
    roblox_client, roblox_user = get_roblox_client(robx_store)

    def robx_token():
        return robx_get_token(robx_client_id, ROBX_SCOPES, robx_options["script.localhost.port"], robx_store)

    roblox_builder = RobloxPresenceBuilder(
        roblox=roblox_client,
        user=roblox_user,
        options=robx_options,
        get_access_token_fn=robx_token,
        client_id=robx_client_id,
    )

    def build_combined():
        activities = []
        try:
            a = roblox_builder.build()
            if a:
                activities.append(a)
        except Exception as e:
            log.error("ropysence's build() failed this cycle, omitting its activity: %s", e)
        try:
            a = standalone_builder.build()
            if a:
                activities.append(a)
        except Exception as e:
            log.error("pysence-standalone's build() failed this cycle, omitting its activity: %s", e)
        return activities

    poll_interval = standalone_options["script.interval"]
    log.info(
        "entering combined main loop (resend_interval=%ss, status=%s, reconnect=%s) -- "
        "connection identity + status/interval/reconnect all come from pysence-standalone's options.txt",
        poll_interval, standalone_options["rpc.presence.status"], standalone_options["script.reconnect.enabled"],
    )

    try:
        asyncio.run(run_gateway_with_reconnect(
            get_access_token_fn=standalone_token,
            build_activity_fn=build_combined,
            poll_interval=poll_interval,
            status=standalone_options["rpc.presence.status"],
            alias=standalone_options["script.dev.alias"],
            reconnect_enabled=standalone_options["script.reconnect.enabled"],
            base_delay=standalone_options["script.reconnect.base_delay"],
            max_delay=standalone_options["script.reconnect.max_delay"],
            max_attempts=standalone_options["script.reconnect.max_attempts"],
        ))
    except KeyboardInterrupt:
        log.info("interrupted by user, shutting down")
    finally:
        roblox_client.close()


if __name__ == "__main__":
    main()
