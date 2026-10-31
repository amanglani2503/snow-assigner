"""Read-only discovery of your ServiceNow setup. It only makes GET requests.

It finds your assignment groups and their active members, lists the incident state values,
counts open unassigned tickets per group, and writes everything the app needs to a mapping file.
No ticket text is read and the password is never printed or saved.

Usage (cmd):
    set SNOW_BASE_URL=https://yourcompany.service-now.com
    set SNOW_USER=integration_user
    python snow_discover.py IDAHO EMP DBE DB-LEGI1 DB-LEGI2 NGA "Your L2 group name"

The password is asked for when you run it (or read from SNOW_PASSWORD).
Options: --show-members  also print member names, --out FILE  mapping file (default snow_mapping.json)
"""
import base64
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class SnowError(Exception):
    pass


def val(field):
    """Machine value of a ServiceNow field. Handles plain strings, {'value', 'link'} references
    and {'value', 'display_value'} pairs (sysparm_display_value=all)."""
    if isinstance(field, dict):
        return field.get("value", "") or ""
    return field or ""


def shown(field):
    """Human-readable value (display_value when present)."""
    if isinstance(field, dict):
        return field.get("display_value") or field.get("value", "") or ""
    return field or ""


class Snow:
    def __init__(self, base_url, user, password, timeout=30):
        self.base = base_url.rstrip("/") + "/"
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "snow-assigner-discover"}
        self.timeout = timeout

    def get(self, path, **params):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers=self.headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            hint = {401: "wrong user name or password",
                    403: "the user lacks a role or ACL for this table",
                    404: "table or path not found",
                    429: "rate limited, wait and retry"}.get(e.code, "unexpected response")
            raise SnowError(f"HTTP {e.code} on {path}: {hint}") from None
        except urllib.error.URLError as e:
            raise SnowError(f"cannot reach {self.base} ({e.reason}). Check the URL, VPN and IP allow-list") from None

    def get_all(self, path, page=200, max_pages=50, **params):
        rows = []
        for i in range(max_pages):
            data = self.get(path, sysparm_limit=page, sysparm_offset=i * page, **params)
            batch = data.get("result", [])
            rows += batch
            if len(batch) < page:
                break
        return rows


def count_open_unassigned(snow, group_sys_id):
    try:
        data = snow.get("api/now/stats/incident", sysparm_count="true",
                        sysparm_query=f"assignment_group={group_sys_id}^active=true^assigned_toISEMPTY")
        return int(data["result"]["stats"]["count"])
    except (SnowError, KeyError, ValueError, TypeError):
        return None


def discover(snow, group_names):
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "instance": snow.base.rstrip("/"), "table": "incident",
              "groups": {}, "members": [], "missing_groups": [], "states": []}

    for name in group_names:
        rows = snow.get_all("api/now/table/sys_user_group", sysparm_query=f"name={name}",
                            sysparm_fields="sys_id,name,active", sysparm_display_value="all")
        rows = sorted(rows, key=lambda r: val(r.get("active")) != "true")        # active groups first
        if not rows:
            result["missing_groups"].append(name)
            continue
        gid = val(rows[0]["sys_id"])
        result["groups"][name] = {"sys_id": gid, "active": val(rows[0].get("active")) == "true",
                                  "duplicates": len(rows) - 1,
                                  "open_unassigned": count_open_unassigned(snow, gid)}

        links = snow.get_all("api/now/table/sys_user_grmember", sysparm_query=f"group={gid}",
                             sysparm_fields="user", sysparm_display_value="all")
        user_ids = sorted({val(r["user"]) for r in links if val(r.get("user"))})
        for i in range(0, len(user_ids), 50):                                     # look users up in chunks
            chunk = user_ids[i:i + 50]
            for u in snow.get_all("api/now/table/sys_user", sysparm_query="sys_idIN" + ",".join(chunk),
                                  sysparm_fields="sys_id,user_name,name,email,active",
                                  sysparm_display_value="all"):
                if val(u.get("active")) == "false":
                    continue
                result["members"].append({
                    "group": name, "sys_user_id": val(u["sys_id"]), "user_name": val(u.get("user_name")),
                    "name": shown(u.get("name")), "email": val(u.get("email"))})

    choices = snow.get_all("api/now/table/sys_choice", sysparm_query="name=incident^element=state^inactive=false",
                           sysparm_fields="value,label")
    result["states"] = [{"value": val(c["value"]), "label": val(c["label"])} for c in choices]
    return result


def summary(result, show_members=False):
    lines = [f"Instance: {result['instance']}", ""]
    for name, g in result["groups"].items():
        n = sum(1 for m in result["members"] if m["group"] == name)
        un = "n/a" if g["open_unassigned"] is None else g["open_unassigned"]
        flags = ("" if g["active"] else "  [INACTIVE]") + (f"  [{g['duplicates']} duplicate name(s)]" if g["duplicates"] else "")
        lines.append(f"  {name:<14} members: {n:<3} open unassigned tickets: {un}{flags}")
        if show_members:
            lines += [f"      {m['name']} ({m['user_name']})" for m in result["members"] if m["group"] == name]
    if result["missing_groups"]:
        lines += ["", "NOT FOUND (check the exact group name): " + ", ".join(result["missing_groups"])]
    lines += ["", "Incident states: " + ", ".join(f"{s['value']}={s['label']}" for s in result["states"])]
    return "\n".join(lines)


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    show_members = "--show-members" in argv
    out = "snow_mapping.json"
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    names = [a for a in argv if not a.startswith("--")]
    if not names:
        print(__doc__)
        return 2
    base, user = env.get("SNOW_BASE_URL", ""), env.get("SNOW_USER", "")
    if not base or not user:
        print("Set SNOW_BASE_URL and SNOW_USER first (see usage above).")
        return 2
    if not base.startswith("https://"):
        print("Warning: the URL does not start with https://")
    password = env.get("SNOW_PASSWORD") or getpass.getpass(f"Password for {user}: ")
    try:
        result = discover(Snow(base, user, password), names)
    except SnowError as e:
        print("FAILED:", e)
        return 1
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(summary(result, show_members))
    print(f"\nSaved {out}. It contains user ids and names, so keep it private.")
    return 0 if not result["missing_groups"] else 3


if __name__ == "__main__":
    sys.exit(main())