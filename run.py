#!/usr/bin/env python3
"""
Entry point. Run this from the repo root:

    python run.py

First run:
  - creates options.txt in THIS directory and exits so you can set
    script.user.id (your Discord Application ID)
  - second run opens a browser for Discord authorization (PKCE, no
    password/token ever typed into this script) and stores the resulting
    tokens encrypted under ~/.config/pysence-standalone/

Every run after that reuses the stored tokens silently until they expire
or you delete them -- see pysence_standalone/core/secure_store.py
(SecureStore().delete("discord_tokens")), or just delete the config dir.

There is nothing to monitor by default: this doesn't poll any account or
service unless you add an api.<name>=standalone.http(...) field to
options.txt yourself (with interval=N to poll repeatedly, or without it to
just fetch once at startup).
"""

import os
import sys

# Add the repo root to sys.path so `pysence_standalone` resolves as a package
# the current working directory this is launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pysence_standalone.app import main

if __name__ == "__main__":
    main()
