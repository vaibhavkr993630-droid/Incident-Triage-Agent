"""service_health: is each service up, and what does its own health check say?"""

import httpx

from triage.tools.base import Tool, ToolError
from triage.tools.logs import clean
from triage.tools.metrics import NoArgs, num

HEALTH_TIMEOUT_S = 5


class ServiceHealth(Tool):
    name = "service_health"
    description = (
        "Current health of every lab service: whether Prometheus can scrape it, and the "
        "HTTP status and body of its /health endpoint. shop-api's /health also checks its "
        "dependencies postgres and redis, which have no health endpoint of their own. "
        "A failing health check is a symptom; it does not by itself name the cause."
    )
    Args = NoArgs

    def _scrape_status(self) -> dict[str, bool]:
        body = self.ctx.get_json(
            f"{self.ctx.endpoints.prometheus}/api/v1/query",
            {"query": "up"},
            what="Prometheus",
        )
        return {s["metric"]["job"]: num(s["value"][1]) == 1 for s in body["data"]["result"]}

    def _health(self, url: str) -> dict:
        try:
            resp = self.ctx.http.get(f"{url}/health", timeout=HEALTH_TIMEOUT_S)
        except httpx.HTTPError as exc:
            return {"http_status": None, "error": f"unreachable: {type(exc).__name__}"}
        try:
            body = resp.json()
        except ValueError:
            body = clean(resp.text)
        return {"http_status": resp.status_code, "body": body}

    def run(self, args: NoArgs) -> dict:
        try:
            scraped = self._scrape_status()
        except ToolError as exc:
            scraped = {}
            scrape_error = str(exc)
        else:
            scrape_error = None
        services = []
        for name, url in self.ctx.endpoints.services.items():
            services.append({"service": name, "scrape_up": scraped.get(name), **self._health(url)})
        payload = {"services": services}
        if scrape_error:
            payload["scrape_error"] = scrape_error
        return payload
