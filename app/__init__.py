"""KB TradeFlow Twin application package."""

import os

# This application does not use third-party Pydantic plugins. Disabling plugin
# discovery avoids scanning every installed distribution during each Studio
# graph import, which is especially costly in the packaged environment.
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")
