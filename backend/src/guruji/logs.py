"""Logging setup and PII-safe helpers.

Never log message text, names or birth details. Refer to users by `user_tag()`.
"""

import hashlib
import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Graph API call is noise


def user_tag(wa_id: str) -> str:
    """Short stable non-reversible tag for a WhatsApp ID, safe for logs and alerts."""
    return "u_" + hashlib.sha256(wa_id.encode()).hexdigest()[:10]
