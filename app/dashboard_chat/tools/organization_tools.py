"""Read-only wrappers for organization-wide ASP.NET API data."""

from typing import Any, Dict, List, Optional

from app.dashboard_chat.services.aspnet_client import aspnet_client


def _records(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "records", "result", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


async def _get_list(endpoint: str) -> List[Dict[str, Any]]:
    try:
        return _records((await aspnet_client.get(endpoint)).json())
    except Exception:
        return []


async def get_organizations() -> List[Dict[str, Any]]:
    return await _get_list("api/UserManagement/GetAllEntity")


async def get_departments(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    return await _get_list(f"api/Department/GetAllActive/{entity_id},{block_id}")


async def get_designations(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    return await _get_list(f"api/Designation/GetAllActive/{entity_id},{block_id}")


async def get_employees(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    return await _get_list(f"api/UserManagement/GetAllActive/{entity_id},{block_id}")


async def get_clients(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    return await _get_list(f"api/Client/GetAll/{entity_id},{block_id}")


async def get_changes(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    return await _get_list(f"api/ChangeTracker/GetAll/{entity_id},{block_id}")


async def get_file_logs(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    return await _get_list(f"api/FileOpenCloseLog/GetAll/{entity_id},{block_id}")


async def get_project_documents(project_id: Optional[str]) -> List[Dict[str, Any]]:
    if not project_id:
        return []
    return await _get_list(f"api/FileManagement/GetFolderList/{project_id}")
