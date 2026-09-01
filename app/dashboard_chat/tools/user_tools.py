import asyncio
import datetime
from typing import List, Dict, Any
from app.dashboard_chat.services.aspnet_client import aspnet_client
from app.dashboard_chat.tools.ticket_tools import get_tickets, get_tickets_created_by_user

async def get_active_users(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    """Fetches list of active users/employees for resolving names to user IDs."""
    try:
        response = await aspnet_client.get(f"api/UserManagement/GetAllActive/{entity_id},{block_id}")
        return response.json()
    except Exception:
        return []

async def get_user_workloads() -> Dict[str, Any]:
    """Fetches employee workload statistics (active, total, pending-on-server, pending-to-close tickets)."""
    try:
        response = await aspnet_client.get("api/Dashboard/GetUserTicketCount")
        return response.json()
    except Exception:
        return {"data": [], "raisedData": []}


async def get_user_ticket_counts(
    user_id: str,
    entity_id: str,
    block_id: str,
) -> Dict[str, Any]:
    """Fetch ticket counts for exactly one employee and organization scope."""
    try:
        response = await aspnet_client.get(
            f"api/Dashboard/GetUserTicketsCount/{user_id},{entity_id},{block_id}"
        )
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def get_employee_current_workload(
    user_id: str,
    entity_id: str,
    block_id: str,
) -> Dict[str, Any]:
    """Return only the employee-assigned fields from the current dashboard snapshot."""
    response = await aspnet_client.get(
        f"api/Dashboard/GetAllTicketCount/{user_id},{entity_id},{block_id}"
    )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Employee workload API returned an unexpected response shape")

    workload = {
        "inProcess": int(data.get("inProcessTicketswihtUserId") or 0),
        "completed": int(data.get("completedTicketsDashboardTL") or 0),
        "closed": int(data.get("closedTicketsDashboardTL") or 0),
        "reopened": int(data.get("reOpenTicketswihtUserId") or 0),
        "overdue": int(data.get("overdueTicketsTL") or 0),
    }
    return {
        "asOfDate": datetime.date.today().isoformat(),
        "totalAssigned": sum(workload.values()),
        **workload,
    }


def _ticket_summary(tickets: List[Dict[str, Any]]) -> Dict[str, Any]:
    statuses = {
        "raised": 0,
        "inProcess": 0,
        "completed": 0,
        "closed": 0,
        "reopened": 0,
        "rejected": 0,
        "overdue": 0,
        "pendingOnServer": 0,
        "reopenPendingOnServer": 0,
        "overduePendingOnServer": 0,
        "other": 0,
    }
    status_map = {
        "raised": "raised",
        "in process": "inProcess",
        "completed": "completed",
        "closed": "closed",
        "re open": "reopened",
        "reopened": "reopened",
        "rejected": "rejected",
        "overdue": "overdue",
        "pending on server": "pendingOnServer",
        "reopen pending on server": "reopenPendingOnServer",
        "overdue pending on server": "overduePendingOnServer",
    }
    for ticket in tickets:
        status = str(ticket.get("status") or "").strip().lower()
        statuses[status_map.get(status, "other")] += 1

    active = sum(
        statuses[key]
        for key in (
            "raised",
            "inProcess",
            "reopened",
            "overdue",
            "pendingOnServer",
            "reopenPendingOnServer",
            "overduePendingOnServer",
        )
    )
    return {
        "total": len(tickets),
        "active": active,
        "completed": statuses["completed"],
        "closed": statuses["closed"],
        "overdue": statuses["overdue"] + statuses["overduePendingOnServer"],
        "pendingOnServer": (
            statuses["pendingOnServer"]
            + statuses["reopenPendingOnServer"]
            + statuses["overduePendingOnServer"]
        ),
        "statusBreakdown": statuses,
    }


async def get_scoped_employee_performance(
    *,
    employee: Dict[str, Any],
    user_id: str,
    entity_id: str,
    block_id: str,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """Build a performance payload without loading any other employee's metrics."""
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must not be before start_date")

    assigned_result, created_tickets = await asyncio.gather(
        get_tickets(
            entity_id=entity_id,
            block_id=block_id,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            for_report=True,
            employee_scope=True,
        ),
        get_tickets_created_by_user(
            user_id=user_id,
            entity_id=entity_id,
            block_id=block_id,
            start_date=start_date,
            end_date=end_date,
        ),
    )
    assigned_tickets = assigned_result.get("tickets") or []
    assigned_total = int(assigned_result.get("totalCount") or 0)
    if assigned_total != len(assigned_tickets):
        raise RuntimeError("Employee performance requires a complete assigned-ticket result")

    employee_name = (
        employee.get("name")
        or employee.get("Name")
        or employee.get("userName")
        or employee.get("UserName")
        or "Employee"
    )
    designation = employee.get("designationName") or employee.get("DesignationName")
    return {
        "employeeName": employee_name,
        "designation": designation,
        "period": {
            "startDate": start_date,
            "endDate": end_date,
            "appliedToData": True,
        },
        "assignedTickets": _ticket_summary(assigned_tickets),
        "createdTickets": _ticket_summary(created_tickets),
    }
