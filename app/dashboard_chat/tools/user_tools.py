import datetime
from typing import List, Dict, Any
from app.dashboard_chat.services.aspnet_client import aspnet_client

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


async def get_employee_performance(
    user_id: str,
    user_name: str,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """Fetch one employee's ticket metrics from the user-wise dashboard API."""
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must not be before start_date")

    workloads = await get_user_workloads()
    normalized_name = user_name.split("(", 1)[0].strip().lower()

    def matching_metric(rows: Any) -> Dict[str, Any]:
        for row in rows if isinstance(rows, list) else []:
            metric_name = str(row.get("userName") or "").split("(", 1)[0].strip().lower()
            if metric_name == normalized_name:
                return row
        return {}

    workload = matching_metric(workloads.get("data"))
    raised = matching_metric(workloads.get("raisedData"))
    return {
        "employeeName": workload.get("userName") or raised.get("userName") or user_name,
        "designation": workload.get("designation"),
        "startDate": start_date,
        "endDate": end_date,
        "workActivityCount": None,
        "activeWorkDays": None,
        "workActivityAvailable": False,
        "tickets": {
            "activeTickets": workload.get("activeTickets", 0),
            "totalTickets": workload.get("totalTickets", 0),
            "pendingToCloseTickets": workload.get("pendingToCloseTickets", 0),
            "pendingOnServer": workload.get("pendingOnServer", 0),
            "raisedTickets": raised.get("raisedTickets", 0),
        },
    }
