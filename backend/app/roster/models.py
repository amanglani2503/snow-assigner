from dataclasses import dataclass


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    group: str
    skills: tuple = ()
    location: str = "Noida"


GROUPS = ["IDAHO", "EMP", "DBE", "DB-LEGI1", "DB-LEGI2", "NGA"]

# Mock locations. Location-wide company holidays (CH) are only applied on days when every
# group still has a morning and an evening person, so keep one location (Noida here) that
# holds the "single" member of each 3-person group.
MEMBERS = [
    Member("idaho-1", "Aarav Sharma", "IDAHO", ("access", "sso"), "Pune"),
    Member("idaho-2", "Diya Verma", "IDAHO", ("access", "ldap"), "Noida"),
    Member("idaho-3", "Kabir Singh", "IDAHO", ("sso", "mfa"), "Bengaluru"),
    Member("emp-1", "Ishaan Gupta", "EMP", ("payroll", "hr-apps"), "Pune"),
    Member("emp-2", "Meera Nair", "EMP", ("hr-apps", "onboarding"), "Noida"),
    Member("emp-3", "Rohan Das", "EMP", ("payroll", "reports"), "Bengaluru"),
    Member("dbe-1", "Ananya Iyer", "DBE", ("oracle", "backup"), "Noida"),
    Member("dbe-2", "Vihaan Reddy", "DBE", ("postgres", "tuning"), "Noida"),
    Member("legi1-1", "Saanvi Joshi", "DB-LEGI1", ("legacy-db", "migration"), "Noida"),
    Member("legi1-2", "Arjun Mehta", "DB-LEGI1", ("legacy-db", "batch-jobs"), "Noida"),
    Member("legi2-1", "Kavya Pillai", "DB-LEGI2", ("legacy-db", "reports"), "Noida"),
    Member("legi2-2", "Aditya Rao", "DB-LEGI2", ("legacy-db", "etl"), "Noida"),
    Member("nga-1", "Tara Menon", "NGA", ("network", "firewall"), "Pune"),
    Member("nga-2", "Yash Kulkarni", "NGA", ("network", "vpn"), "Noida"),
    Member("nga-3", "Riya Bose", "NGA", ("vpn", "dns"), "Bengaluru"),
]