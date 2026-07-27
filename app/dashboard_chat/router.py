import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status

from app.dashboard_chat.schemas import (
    DashboardChatRequest, 
    DashboardChatResponse, 
    ConversationState, 
    FiltersState
)
from app.dashboard_chat.memory import state_store
from app.dashboard_chat.tools.project_tools import get_active_projects, get_active_categories
from app.dashboard_chat.tools.user_tools import get_active_users
from app.dashboard_chat.chains.planner_chain import run_planner_chain
from app.dashboard_chat.chains.answer_chain import run_answer_chain
from app.dashboard_chat.plan_executor import execute_plan

router = APIRouter(prefix="/api/assistant/admin", tags=["dashboard-chat"])

SOURCE_LABELS = {
    "search_tickets": "Ticket Registration",
    "get_ticket_history": "Ticket Registration",
    "get_dashboard_counts": "Dashboard",
    "get_priority_counts": "Dashboard",
    "get_project_ticket_counts": "Project Management",
    "get_user_workloads": "Dashboard",
    "list_organizations": "Organization",
    "list_departments": "Department",
    "list_designations": "Designation",
    "search_employees": "Employee",
    "search_projects": "Project Management",
    "search_clients": "CRM",
    "search_changes": "Change Tracker",
    "search_documents": "ShareDoc",
    "get_file_log_summary": "ShareDoc",
}


def response_metadata(tool_results: list[Dict[str, Any]], args: Dict[str, Any]) -> tuple[list[str], Dict[str, Any]]:
    sources = list(dict.fromkeys(
        SOURCE_LABELS.get(result.get("tool"), result.get("tool"))
        for result in tool_results
        if result.get("type") == "tool_result"
    ))
    filters = {
        key: value for key, value in args.items()
        if value is not None and key not in {"pageIndex", "pageSize"} and not key.startswith("_")
    }
    return sources, filters


def conversation_recap(message: str, state: ConversationState) -> Optional[str]:
    lower = message.lower()
    if not any(phrase in lower for phrase in ("what did we discuss", "what did we say", "previous conversation")):
        return None
    prior_questions = [turn.text for turn in state.recentTurns if turn.role == "user"]
    if not prior_questions:
        return "We have not discussed anything in this chat yet."
    topic_words = {word for word in lower.replace("?", "").split() if len(word) > 3} - {"what", "discuss", "previous", "conversation"}
    relevant = [question for question in prior_questions if not topic_words or any(word in question.lower() for word in topic_words)]
    questions = (relevant or prior_questions)[-3:]
    return "Previously, you asked about:\n" + "\n".join(f"- {question}" for question in questions)


def format_grouped_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    groups = data.get("groups") or []
    total_count = data.get("totalCount", 0)
    grouped_row_count = data.get("groupedRowCount", total_count)
    group_by = data.get("groupBy") or "group"
    group_label_map = {
        "projectName": "project",
        "assignedToName": "assignee",
        "categoryName": "category",
        "priority": "priority",
        "status": "status",
    }
    label = group_label_map.get(group_by, group_by)

    if not groups:
        return {
            "message": f"I found {total_count} matching tickets, but there was no {label} value to group.",
            # "suggestions": ["Try another date range", "Group by user", "Group by category"],
        }

    top = groups[0]
    lines = [
        f"**{item.get('group')}**: {item.get('count')}"
        for item in groups[:10]
    ]
    return {
        "message": (
            f"The {label} with the most matching tickets is **{top.get('group')}** "
            f"with **{top.get('count')}** tickets.\n\n"
            f"Overall, I found **{total_count}** matching tickets. Breakdown by {label}:\n"
            + "\n".join(f"- {line}" for line in lines)
        ),
        # "suggestions": ["Show ticket list", "Group by assignee", "Only high priority"],
    }


def format_monthly_ticket_count(data: Dict[str, Any]) -> Dict[str, Any]:
    month = data.get("month")
    year = data.get("year")
    count = data.get("count")

    if month and year and count is not None:
        return {
            "message": f"I found **{count}** tickets raised in **{month} {year}**.",
            # "suggestions": ["Show current raised tickets", "Show this month", "Show overdue tickets"],
        }

    return {
        "message": "I could not identify a single month for this ticket count. Please ask for a specific month and year.",
        # "suggestions": ["May 2026", "This month", "Last month"],
    }


def format_employee_performance(data: Dict[str, Any]) -> Dict[str, Any]:
    tickets = data.get("tickets") or {}
    total = int(tickets.get("totalTickets") or 0)
    raised = int(tickets.get("raisedTickets") or 0)
    pending_close = int(tickets.get("pendingToCloseTickets") or 0)
    pending_server = int(tickets.get("pendingOnServer") or 0)

    if not total:
        return {
            "message": (
                f"I found no ticket activity for **{data.get('employeeName')}** "
                f"between **{data.get('startDate')} and {data.get('endDate')}**."
            ),
        }

    pending_rate = round(pending_close * 100 / total)
    raised_rate = round(raised * 100 / total)
    insights = [
        (
            f"Of the **{total}** total tickets, **{pending_close}** are still pending closure "
            f",about **{pending_rate}%** of the total needs follow-up."
        ),
        (
            f"**{raised}** of **{total}** tickets ({raised_rate}%) were raised by "
            f"{data.get('employeeName')}; this suggests most ticket volume was self-raised "
            f"rather than assigned or inbound, assuming “raised” means initiated by the employee."
        ),
        (
            f"Only **{pending_server}** ticket{' is' if pending_server == 1 else 's are'} pending on server, "
            f"a {'small' if pending_server < pending_close else 'material'} bottleneck compared with the "
            f"pending-closure queue."
        ),
    ]
    if data.get("workActivityAvailable"):
        activity_days = int(data.get("activeWorkDays") or 0)
        activity_count = int(data.get("workActivityCount") or 0)
        insights.append(
            f"Work activity shows **{activity_count}** actions across **{activity_days}** active days."
        )
    return {
        "message": "\n".join(f"- {insight}" for insight in insights),
    }


def is_count_only_query(query: str) -> bool:
    lower = (query or "").lower()
    count_terms = ["how many", "count", "total", "number of"]
    list_terms = ["show", "list", "display", "give me", "details", "first", "next", "which", "who", "whose", "highest", "most", "maximum", "breakdown", "group"]
    return any(term in lower for term in count_terms) and not any(term in lower for term in list_terms)


def format_ticket_count(data: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    total = data.get("totalCount", 0)
    status_value = args.get("status")
    label = f" with status **{status_value}**" if status_value else ""
    return {
        "message": f"I found **{total}** tickets{label}.",
        # "suggestions": ["Show ticket list", "Group by project", "Group by assignee"],
    }

async def resolve_lookup_ids(
    entity_id: str,
    block_id: str,
    project_name: Optional[str] = None,
    user_name: Optional[str] = None,
    category_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """Resolves natural language project, category or user names to their respective GUIDs."""
    results = {
        "projectId": None,
        "assignedTo": None,
        "categoryId": None
    }

    # 1. Resolve Project
    if project_name:
        projects = await get_active_projects(entity_id, block_id)
        project_name_lower = project_name.lower().replace("project", "").strip()
        for p in projects:
            p_name = p.get("name") or p.get("projectName") or ""
            if project_name_lower in p_name.lower():
                results["projectId"] = p.get("id") or p.get("projectId")
                break

    # 2. Resolve User / Employee
    if user_name:
        users = await get_active_users(entity_id, block_id)
        user_name_lower = user_name.lower().strip()
        for u in users:
            u_name = u.get("name") or u.get("userName") or ""
            if user_name_lower in u_name.lower():
                results["assignedTo"] = u.get("id") or u.get("userId")
                break

    # 3. Resolve Category
    if category_name:
        categories = await get_active_categories(entity_id, block_id)
        cat_name_lower = category_name.lower().strip()
        for c in categories:
            c_name = c.get("name") or c.get("categoryName") or ""
            if cat_name_lower in c_name.lower():
                results["categoryId"] = c.get("id") or c.get("categoryId")
                break

    return results

@router.post("/dashboard-chat", response_model=DashboardChatResponse)
async def dashboard_chat(request: DashboardChatRequest):
    # 1. Reject oversized requests to prevent prompt injection / context overflow
    # DASHBOARD_CHAT_CONTEXT_LIMIT_KB defaults to 40 KB
    content_kb = len(request.message.encode('utf-8')) / 1024.0
    from app.config import settings
    if content_kb > settings.DASHBOARD_CHAT_CONTEXT_LIMIT_KB:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Message size exceeds limit of {settings.DASHBOARD_CHAT_CONTEXT_LIMIT_KB} KB."
        )

    # 2. Retrieve or generate conversation session state
    conversation_id = request.conversationId
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    state = state_store.get(conversation_id)
    if not state:
        state = ConversationState(
            conversationId=conversation_id,
            userId=request.userId,
            entityId=request.entityId,
            blockId=request.blockId
        )
    elif (state.userId, state.entityId, state.blockId) != (request.userId, request.entityId, request.blockId):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Conversation does not belong to this user scope.")

    recap = conversation_recap(request.message, state)
    if recap is not None:
        state_store.add_turn(state, "user", request.message)
        state_store.add_turn(state, "assistant", recap)
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            traceId=trace_id,
            type="answer",
            message=recap,
            sources=["Conversation"],
        )

    # Convert state to dict for LLM planning context
    prev_state_dict = {
        "lastIntent": state.lastIntent,
        "filters": state.filters.model_dump(),
        "lastTool": state.lastTool,
        "lastArgs": state.lastArgs,
        "lastResultKind": state.lastResultKind,
        "lastResponseFormat": state.lastResponseFormat,
        "pageIndex": state.pageIndex,
        "summary": state.summary,
        "recentTurns": [turn.model_dump() for turn in state.recentTurns[-10:]],
    }

    # 3. Ask the LLM for a dynamic tool plan instead of a fixed intent.
    plan = await run_planner_chain(request.message, prev_state_dict)

    if plan.get("needs_clarification"):
        state_store.add_turn(state, "user", request.message)
        state_store.add_turn(state, "assistant", plan.get("clarification_question") or "Please clarify your request.")
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            traceId=trace_id,
            type="clarification",
            message=plan.get("clarification_question") or "Please clarify your request.",
            # suggestions=plan.get("suggestions") or ["This month", "Last month", "This week"]
        )

    # Follow-up paging: reuse previous args and increment page index.
    if plan.get("isNextPageRequest") and plan.get("tool_calls"):
        for tool_call in plan["tool_calls"]:
            args = tool_call.setdefault("args", {})
            previous_page = int(state.lastArgs.get("pageIndex") or state.pageIndex or 1)
            args["pageIndex"] = previous_page + 1

    try:
        tool_results, executed_state = await execute_plan(
            plan,
            user_id=request.userId,
            entity_id=request.entityId,
            block_id=request.blockId,
            trace_id=trace_id,
        )
    except Exception as e:
        return DashboardChatResponse(
            conversationId=conversation_id,
            type="answer",
            message="I could not fetch the requested data right now. Please try again.",
            # suggestions=["Retry"]
        )

    for result in tool_results:
        if result.get("type") == "clarification":
            state_store.add_turn(state, "user", request.message)
            state_store.add_turn(state, "assistant", result.get("message", "Please clarify your request."))
            state_store.set(conversation_id, state)
            return DashboardChatResponse(
                conversationId=conversation_id,
                traceId=trace_id,
                type="clarification",
                message=result.get("message", "Please clarify your request."),
                # suggestions=result.get("suggestions") or ["This month", "Last month", "This week"]
            )

    if not tool_results:
        state_store.add_turn(state, "user", request.message)
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            type="answer",
            message="I can help with tickets, projects, users, workload, and dashboard analytics. What would you like to check?",
            # suggestions=["Show overdue tickets", "Project health", "User workload"]
        )

    failed_result = next((result for result in reversed(tool_results) if result.get("type") == "error"), None)
    if failed_result:
        return DashboardChatResponse(
            conversationId=conversation_id,
            type="answer",
            message=failed_result.get("message", "I could not complete that request."),
            # suggestions=failed_result.get("suggestions") or ["Try again"],
        )

    final_result = next((r for r in reversed(tool_results) if r.get("type") == "tool_result"), tool_results[-1])
    kind = final_result.get("kind", plan.get("response_format") or "summary")
    tool_data = final_result.get("data")

    # 4. Summarize the executed tool data. Grouped counts are deterministic to avoid LLM count drift.
    if kind == "grouped_ticket_summary" and isinstance(tool_data, dict):
        answer_res = format_grouped_summary(tool_data)
    elif kind == "monthly_ticket_count" and isinstance(tool_data, dict):
        answer_res = format_monthly_ticket_count(tool_data)
    elif kind == "employee_performance" and isinstance(tool_data, dict):
        answer_res = format_employee_performance(tool_data)
    elif kind == "ticket_list" and isinstance(tool_data, dict) and is_count_only_query(request.message):
        answer_res = format_ticket_count(tool_data, final_result.get("args") or {})
    else:
        answer_res = await run_answer_chain(
            query=request.message,
            intent=plan.get("response_format") or kind,
            filters=final_result.get("args") or {},
            api_data={
                "results": tool_results,
                "final": tool_data,
            }
        )

    # 5. Persist reusable state for follow-up turns.
    state.lastIntent = plan.get("response_format") or kind
    state.lastResultKind = executed_state.get("lastResultKind") or kind
    state.lastTool = executed_state.get("lastTool")
    state.lastArgs = executed_state.get("lastArgs") or {}
    state.lastResponseFormat = plan.get("response_format") or kind
    state.pageIndex = int(state.lastArgs.get("pageIndex") or state.pageIndex or 1)
    state_store.add_turn(state, "user", request.message)
    state_store.add_turn(state, "assistant", answer_res.get("message", "Here are the results."))
    state_store.set(conversation_id, state)

    if kind == "ticket_list" and tool_data:
        res_data = {
            "kind": "ticket_list",
            "totalCount": tool_data.get("totalCount", 0),
            "pageIndex": tool_data.get("pageIndex", 1),
            "pageSize": tool_data.get("pageSize", settings.DASHBOARD_CHAT_MAX_PAGE_SIZE),
            "tickets": tool_data.get("tickets") or []
        }
    else:
        res_data = {
            "kind": kind,
            "result": tool_data
        }

    sources, applied_filters = response_metadata(tool_results, final_result.get("args") or {})

    return DashboardChatResponse(
        conversationId=conversation_id,
        traceId=trace_id,
        type="answer",
        message=answer_res.get("message", "Here are the results."),
        data=res_data,
        # suggestions=answer_res.get("suggestions"),
        sources=sources,
        appliedFilters=applied_filters,
    )
