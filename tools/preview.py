#!/usr/bin/env python3
"""
Prints the activity dict pysence-standalone would send, WITHOUT ever
touching Discord -- no OAuth, no Gateway connection. Useful for iterating
on options.txt: does my api.* field resolve? does this button show up?

    python tools/preview.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.logging_setup import setup_logging, apply_options
from src.core.options import load_options
from src.core.http_api import HttpApiManager
from src.presence_builder import PresenceBuilder

setup_logging()


def _fake_token():
    # No real Discord call is made unless rpc.activity.image resolves to a
    # URL that needs proxying -- in that case this preview intentionally
    # skips the proxy step and just shows which URL WOULD have been sent.
    return "preview-mode-no-token"


def main():
    options = load_options()
    apply_options(options)

    api_manager = HttpApiManager(options["_api_fields"])
    api_manager.start()

    builder = PresenceBuilder(
        options=options,
        api_manager=api_manager,
        get_access_token_fn=_fake_token,
        client_id=options["script.user.id"] or "0000000000000000",
    )

    print("\n--- resolved placeholders ---")
    print(json.dumps(builder._context(), indent=2))

    print("\n--- activity payload ---")
    try:
        activity = builder.build()
        print(json.dumps(activity, indent=2))
    except Exception as e:
        print(f"NOTE: image proxying needs a real Discord token, so this may fail on rpc.activity.image "
              f"if it's set to a URL. Everything else still resolved above. Error: {e}")


if __name__ == "__main__":
    main()
