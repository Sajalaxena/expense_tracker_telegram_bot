"""Entry point for running the Tracksy bot."""

import os
import shutil

# On cloud: if config.json doesn't exist, copy from config.prod.json
if not os.path.exists("config.json") and os.path.exists("config.prod.json"):
    shutil.copy("config.prod.json", "config.json")

from src.bot import main

if __name__ == "__main__":
    main()
