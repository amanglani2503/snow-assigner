import httpx

from app import config


class SnowError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class ServiceNowClient:
    """Async ServiceNow Table API client. Same code for the mock and a real instance:
    only base_url and credentials differ."""

    def __init__(self, base_url=None, auth=None, transport=None, timeout=10.0):
        self._kw = {
            "base_url": (base_url or config.SNOW_BASE_URL).rstrip("/") + "/",
            "auth": auth or config.snow_auth(),
            "transport": transport,          # tests pass an in-process ASGI transport
            "timeout": timeout,
            "headers": {"Accept": "application/json"},
        }

    async def _request(self, method: str, path: str, **kw):
        async with httpx.AsyncClient(**self._kw) as c:
            r = await c.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()["error"]
                msg = body["message"] + (f": {body['detail']}" if body.get("detail") else "")
            except Exception:
                msg = r.text
            raise SnowError(msg, r.status_code)
        return r.json()["result"]

    async def get_incident(self, sys_id: str) -> dict:
        return await self._request("GET", f"api/now/table/incident/{sys_id}")

    async def list_unassigned(self, group: str) -> list[dict]:
        rows = await self._request("GET", "api/now/table/incident", params={
            "sysparm_query": f"assignment_group={group}^assigned_toISEMPTY"})
        return [r for r in rows if r.get("u_in_service_hours")]   # out-of-hours tickets are ignored

    async def get_group_members(self, group: str) -> list[dict]:
        rows = await self._request("GET", "api/now/table/sys_user_grmember",
                                   params={"sysparm_query": f"group={group}"})
        return [{"id": r["user"]["value"], "name": r["user"]["display_value"]} for r in rows]

    async def assign(self, sys_id: str, member_id: str, work_notes: str = "", actor: str = "agent") -> dict:
        return await self._request(
            "PATCH", f"api/now/table/incident/{sys_id}",
            json={"assigned_to": member_id, "work_notes": work_notes},
            headers={"X-Actor": actor},
        )

    async def list_assigned_to(self, member_id: str) -> list[dict]:
        return await self._request("GET", "api/now/table/incident", params={
            "sysparm_query": f"assigned_to={member_id}", "sysparm_limit": 1000})

    async def ignore(self, sys_id: str, work_notes: str = "", actor: str = "agent") -> dict:
        return await self._request(
            "PATCH", f"api/now/table/incident/{sys_id}",
            json={"u_ignore": True, "work_notes": work_notes},
            headers={"X-Actor": actor},
        )