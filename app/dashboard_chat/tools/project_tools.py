from typing import List, Dict, Any
from app.dashboard_chat.services.aspnet_client import aspnet_client

async def get_active_projects(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    """Fetches list of active projects for resolving names to project IDs."""
    try:
        response = await aspnet_client.get(f"api/Project/GetAllActive/{entity_id},{block_id}")
        return response.json()
    except Exception:
        return []

async def get_project_ticket_counts() -> List[Dict[str, Any]]:
    """Fetches project-wise active, total, and pending-on-server ticket counts."""
    try:
        response = await aspnet_client.get("api/Dashboard/GetProjectTicketCount")
        return response.json()
    except Exception:
        return []

async def get_active_categories(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    """Fetches list of active categories for resolving names to category IDs."""
    try:
        response = await aspnet_client.get(f"api/Category/GetAllActive/{entity_id},{block_id}")
        return response.json()
    except Exception:
        return []

