import datetime
import logging
import time
from typing import Any, Dict, List, Tuple

from app.dashboard_chat.tool_registry import EMPLOYEE_TOOL_NAMES, TOOL_CATALOG, build_langchain_tools


ALLOWED_TOOLS = {tool["name"] for tool in TOOL_CATALOG}
logger = logging.getLogger(__name__)


def _safe_args(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value for key, value in args.items()
        if key in {"status", "startDate", "endDate", "priority", "projectId", "categoryId", "assignedTo", "pageIndex", "pageSize"}
    }


async def execute_plan(
    plan: Dict[str, Any],
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
    trace_id: str = "",
    employee_scope: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    last_executed: Dict[str, Any] = {}
    tools = {
        tool.name: tool
        for tool in build_langchain_tools(
            user_id=user_id,
            entity_id=entity_id,
            block_id=block_id,
            employee_scope=employee_scope,
        )
    }
    allowed_tools = EMPLOYEE_TOOL_NAMES if employee_scope else ALLOWED_TOOLS

    for raw_call in plan.get("tool_calls") or []:
        tool_name = raw_call.get("tool")
        args = raw_call.get("args") or {}
        if tool_name not in allowed_tools or tool_name not in tools:
            results.append({
                "type": "access_denied" if employee_scope else "error",
                "message": (
                    "The employee chatbot supports your tickets and your performance information."
                    if employee_scope
                    else f"I cannot use the requested tool: {tool_name}."
                ),
                # "suggestions": ["Show overdue tickets", "Project health", "User workload"],
            })
            if employee_scope:
                break
            continue

        started = time.monotonic()
        result = await tools[tool_name].ainvoke(args)
        duration_ms = round((time.monotonic() - started) * 1000)
        if result.get("type") == "tool_result" and isinstance(result.get("data"), dict):
            result["data"].setdefault("sourceTimestamp", datetime.datetime.now(datetime.timezone.utc).isoformat())
        result_count = (
            (result.get("data") or {}).get("totalCount", 0)
            if isinstance(result.get("data"), dict) else 0
        )
        logger.info(
            "dashboard_chat_tool trace=%s tool=%s args=%s duration_ms=%s result_count=%s",
            trace_id, tool_name, _safe_args(result.get("args") or args), duration_ms, result_count,
        )
        results.append(result)

        if result.get("type") == "access_denied":
            break

        if result.get("type") == "tool_result":
            last_executed = {
                "lastTool": tool_name,
                "lastArgs": result.get("args") or args,
                "lastResultKind": result.get("kind"),
            }
        elif result.get("type") == "clarification":
            break

    return results, last_executed
