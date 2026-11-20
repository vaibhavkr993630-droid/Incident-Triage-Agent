"""list_recent_changes: the deploy and config-change history.

The lab serves its change log from shop-api's ``GET /_changes``. That is the one control-plane
route the agent may read: it is the lab's stand-in for a deploy tool's history API. The agent
never calls any other ``/_`` route (those set fault flags).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from triage.tools.base import Tool, fit_list
from triage.tools.logs import clean


class ListChangesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(
        default=1440, ge=1, le=10080, description="How far back to look (default 24 hours)."
    )


class ListRecentChanges(Tool):
    name = "list_recent_changes"
    description = (
        "Deploys and config changes to the lab services, newest first, with how many minutes "
        "ago each happened. Changes shortly before a problem began are prime suspects, but a "
        "recent change is not proof: check that its effect matches the symptoms."
    )
    Args = ListChangesArgs

    def run(self, args: ListChangesArgs) -> dict:
        rows = self.ctx.get_json(
            f"{self.ctx.endpoints.services['shop-api']}/_changes",
            {"since_minutes": args.minutes},
            what="change log",
        )
        now = self.ctx.clock()
        changes = []
        for row in rows:
            ts = datetime.fromisoformat(row["ts"])
            changes.append(
                {
                    "time": row["ts"],
                    "minutes_ago": round((now - ts).total_seconds() / 60, 1),
                    "service": row["service"],
                    "kind": row["kind"],
                    "summary": clean(row["summary"]),
                    "details": {clean(k, 60): clean(v, 120) for k, v in row["details"].items()},
                }
            )
        return fit_list({"window_minutes": args.minutes, "changes": changes}, "changes")
