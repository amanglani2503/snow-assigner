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
                    try:
                        err = response.json().get("error", {})
                    except ValueError:
                        err = {}
                    raise SnowError(f"{err.get('message', '')} {err.get('detail', '')}".strip() or response.text)
                return self._format_incident(response.json().get("result", {}))
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to assign ticket in ServiceNow: {str(e)}")

    async def get_group_members(self, group: str) -> list:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/sys_user_grmember",
                    params={"sysparm_query": f"group.name={group}^user.active=true",
                            "sysparm_display_value": "all", "sysparm_limit": 200},
                    headers=self.headers)
                response.raise_for_status()
                return [{"id": (r.get("user") or {}).get("value"),
                         "name": (r.get("user") or {}).get("display_value")}
                        for r in response.json().get("result", [])]
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to fetch group members: {e}")

    async def list_active_for_group(self, group: str) -> list:
        """Raw values: datetimes come back as UTC, references as sys_ids."""
        query = f"assignment_group.name={group}^active=true"
        if config.SNOW_EXTRA_QUERY:
            query += "^" + config.SNOW_EXTRA_QUERY
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/incident",
                    params={"sysparm_query": query + "^ORDERBYopened_at", "sysparm_limit": 500,
                            "sysparm_fields": "sys_id,number,short_description,priority,opened_at,assigned_to",
                            "sysparm_exclude_reference_link": "true",
                            "sysparm_display_value": "false"},
                    headers=self.headers)
                response.raise_for_status()
                return response.json().get("result", [])
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to list tickets for {group}: {e}")

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

    async def list_unassigned_tickets(self, group_name: str) -> list:
        """Fetch unassigned active tickets for a specific group from ServiceNow using dot-walking."""
        # Use assignment_group.name so ServiceNow resolves the group name to its sys_id automatically
        query = f"assignment_group.name={group_name}^active=true^assigned_toISEMPTY"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}api/now/table/incident",
                    params={
                        "sysparm_query": query,
                        "sysparm_limit": 100,
                        "sysparm_display_value": "true"  # Tells ServiceNow to return display names for references
                    },
                    headers=self.headers
                )
                response.raise_for_status()
                results = response.json().get("result", [])
                return [self._format_incident(inc) for inc in results]
            except httpx.HTTPError as e:
                raise SnowError(f"Failed to list unassigned tickets: {str(e)}")

    def _format_incident(self, r: dict) -> dict:
        """Robustly extract string values whether fields are raw strings or ServiceNow reference dicts."""

        def val(field):
            if isinstance(field, dict):
                return field.get("display_value") or field.get("value", "") or ""
            return field or ""

        return {
            "sys_id": val(r.get("sys_id")),
            "number": val(r.get("number")),
            "short_description": val(r.get("short_description")),
            "priority": val(r.get("priority")),
            "assignment_group": val(r.get("assignment_group")),
            "opened_at": val(r.get("opened_at")),
            "assigned_to": val(r.get("assigned_to")),
            "u_sla_due": val(r.get("u_sla_due")),
            "u_level": val(r.get("u_level", "L1")),
        }