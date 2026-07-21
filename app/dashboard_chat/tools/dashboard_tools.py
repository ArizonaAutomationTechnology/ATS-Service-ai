from typing import Dict, Any, Optional
from app.dashboard_chat.services.aspnet_client import aspnet_client

async def get_dashboard_counts(user_id: str, entity_id: str, block_id: str) -> Dict[str, Any]:
    """Fetches total ticket counts (raised, completed, overdue, reopen, in progress, etc.)"""
    try:
        response = await aspnet_client.get(f"api/Dashboard/GetAllTicketCount/{user_id},{entity_id},{block_id}")
        return response.json()
    except Exception as e:
        return {"error": str(e)}

async def get_priority_counts() -> Dict[str, Any]:
    """Fetches general high/medium/low priority ticket counts."""
    try:
        response = await aspnet_client.get("api/Dashboard/GetTicketPriorityCount")
        return response.json()
    except Exception as e:
        return {"error": str(e)}

async def get_ticket_count_month_wise() -> Dict[str, Any]:
    """Fetches ticket volume grouped by created year and month, matching the dashboard calendar cards."""
    try:
        response = await aspnet_client.get("api/Dashboard/GetTicketCountMonthWise")
        return response.json()
    except Exception as e:
        return {"error": str(e)}
