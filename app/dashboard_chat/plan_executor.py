from typing import Any, Dict, List, Tuple

from app.dashboard_chat.tool_registry import TOOL_CATALOG, execute_tool


ALLOWED_TOOLS = {tool["name"] for tool in TOOL_CATALOG}


async def execute_plan(
    plan: Dict[str, Any],
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    last_data: Any = None
    last_executed: Dict[str, Any] = {}

    for raw_call in plan.get("tool_calls") or []:
        tool_name = raw_call.get("tool")
        args = raw_call.get("args") or {}
        if tool_name not in ALLOWED_TOOLS:
            results.append({
                "type": "error",
                "message": f"I cannot use the requested tool: {tool_name}.",
                "suggestions": ["Show overdue tickets", "Project health", "User workload"],
            })
            continue

        result = await execute_tool(
            tool_name,
            args,
            user_id=user_id,
            entity_id=entity_id,
            block_id=block_id,
            previous_result=last_data,
        )
        results.append(result)

        if result.get("type") == "tool_result":
            last_data = result
            last_executed = {
                "lastTool": tool_name,
                "lastArgs": result.get("args") or args,
                "lastResultKind": result.get("kind"),
            }
        elif result.get("type") == "clarification":
            break

    return results, last_executed
