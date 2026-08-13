"""Vulture whitelist for Vooglaadija.

Names here are intentionally-unused-but-required by framework contracts.
Vulture suppresses findings by name, so keep entries as narrow as possible
and justify each one. Run: vulture app core worker scripts alembic
--min-confidence 80 scripts/audit/vulture_whitelist.py
"""

# vulture whitelist

# cls -- required classmethod signature for pydantic @field_validator hooks
# (app/schemas/download.py, app/schemas/user.py, core/config.py)
cls = cls  # noqa: F821, PLW0127

# msg -- required parameter of urllib.request.HTTPRedirectHandler.redirect_request
# (app/utils/validators.py SSRF redirect inspector)
msg = msg  # noqa: F821, PLW0127

# method_name -- required processor hook signature for structlog
# (core/logging_config.py add_timestamp, add_service_context, rename_event_key)
method_name = method_name  # noqa: F821, PLW0127
