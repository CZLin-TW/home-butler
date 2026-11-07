"""Why HA's Supervisor calls the system unhealthy, as HA itself lists it under Repairs.

While unhealthy HA keeps running but blocks every automatic update (2026-10-29: an update rolled back
and nothing updated for nine days, unseen). HomeButler announces it on LINE. Read-only."""
import re

from homeassistant.helpers import issue_registry as ir

PREFIX = "unhealthy_system_"          # hassio's issue IDs: unhealthy_system_<reason>
REASON = re.compile(r"^[a-z0-9_]{1,64}$")


def unhealthy(hass) -> list[str]:
    reasons = {issue_id[len(PREFIX):] for (domain, issue_id), issue in ir.async_get(hass).issues.items()
               if domain == "hassio" and issue_id.startswith(PREFIX) and issue.active}
    return sorted(r for r in reasons if REASON.fullmatch(r))[:20]
