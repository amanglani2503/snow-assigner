import base64
import httpx
from app import config


class SnowError(Exception):
    pass


class ServiceNowClient:
    def __init__(self):
        self.base_url = config.SNOW_BASE_URL.rstrip("/") + "/"
        auth = config.snow_auth()
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"

    async def get_incident(self, sys_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/incident/{sys_id}",
                    headers=self.headers
                )
                if response.status_code == 404:
                    raise SnowError(f"Incident {sys_id} not found in ServiceNow")
                response.raise_for_status()
                return self._format_incident(response.json().get("result", {}))
            except httpx.HTTPError as e:
                raise SnowError(f"ServiceNow API error: {str(e)}")

    async def list_unassigned_tickets(self, group_name: str) -> list:
        """Fetch unassigned active tickets for a specific group from ServiceNow."""
        query = f"assignment_group.name={group_name}^active=true^assigned_toISEMPTY"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/incident",
                    params={"sysparm_query": query, "sysparm_limit": 100},
                    headers=self.headers
                )
                response.raise_for_status()
                results = response.json().get("result", [])
                return [self._format_incident(inc) for inc in results]
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to list unassigned tickets: {str(e)}")

    async def assign(self, sys_id: str, member_id: str, work_notes: str = "", actor: str = "agent") -> dict:
        """Make an actual PATCH call to ServiceNow to assign the ticket in production."""
        payload = {
            "assigned_to": member_id,
            "work_notes": work_notes,
            "state": "2"  # 2 = In Progress / Assigned in ServiceNow
        }
        headers = {**self.headers, "X-Actor": actor}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.patch(
                    f"{self.base_url}api/now/table/incident/{sys_id}",
                    json=payload,
                    headers=headers
                )
                if response.status_code >= 400:
                    err_msg = response.json().get("error", {}).get("message", response.text)
                    raise SnowError(err_msg)
                return self._format_incident(response.json().get("result", {}))
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to assign ticket in ServiceNow: {str(e)}")

    async def get_group_members(self, group: str) -> list:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/sys_user_grmember",
                    params={"sysparm_query": f"group.name={group}", "sysparm_display_value": "all"},
                    headers=self.headers
                )
                response.raise_for_status()
                rows = response.json().get("result", [])
                members = []
                for r in rows:
                    user_info = r.get("user", {})
                    members.append({
                        "id": user_info.get("value"),
                        "name": user_info.get("display_value")
                    })
                return members
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to fetch group members: {str(e)}")

    async def list_assigned_to(self, member_id: str) -> list:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/incident",
                    params={"sysparm_query": f"assigned_to={member_id}^active=true"},
                    headers=self.headers
                )
                response.raise_for_status()
                return response.json().get("result", [])
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to list member load: {str(e)}")

    # def _format_incident(self, r: dict) -> dict:
    #     def val(field):
    #         return field.get("value", "") if isinstance(field, dict) else (field or "")
    #
    #     return {
    #         "sys_id": val(r.get("sys_id")),
    #         "number": val(r.get("number")),
    #         "short_description": val(r.get("short_description")),
    #         "priority": val(r.get("priority")),
    #         "assignment_group": val(r.get("assignment_group")),
    #         "opened_at": val(r.get("opened_at")),
    #         "assigned_to": val(r.get("assigned_to")),
    #         "u_sla_due": val(r.get("u_sla_due")),
    #         "u_level": val(r.get("u_level", "L1")),
    #     }

    def _format_incident(r: dict) -> dict:
        def val(field):
            if isinstance(field, dict):
                return field.get("display_value") or field.get("value", "") or ""
            return field or ""

        return {
            "sys_id": val(r.get("sys_id")),
            "number": val(r.get("number")),
            "short_description": val(r.get("short_description")),
            "priority": val(r.get("priority")),
            "assignment_group": val(r.get("assignment_group")),  # Must match group name like "Network" or "IDAHO"
            "opened_at": val(r.get("opened_at")),
            "assigned_to": val(r.get("assigned_to")),
            "u_sla_due": val(r.get("u_sla_due")),
            "u_level": val(r.get("u_level", "L1")),
        }