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
