from typing import Any, Dict

from fastapi import APIRouter, HTTPException, status

from app.dashboard_chat.router import handle_dashboard_chat
from app.dashboard_chat.schemas import DashboardChatRequest, DashboardChatResponse
from app.dashboard_chat.tools.user_tools import get_active_users


router = APIRouter(prefix="/api/assistant/employee", tags=["employee-dashboard-chat"])


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_requested_employee(user: Dict[str, Any], user_id: str) -> bool:
    return (
        _normalized(user.get("id") or user.get("Id") or user.get("userId"))
        == _normalized(user_id)
        and _normalized(user.get("groupName") or user.get("GroupName")) == "employee"
    )


@router.post("/dashboard-chat", response_model=DashboardChatResponse)
async def employee_dashboard_chat(request: DashboardChatRequest):
    if _normalized(request.userRole) != "employee":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employee chatbot access requires an Employee role.",
        )

    users = await get_active_users(request.entityId, request.blockId)
    if not any(_is_requested_employee(user, request.userId) for user in users):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The employee identity could not be verified in this organization scope.",
        )

    return await handle_dashboard_chat(request, employee_scope=True)
