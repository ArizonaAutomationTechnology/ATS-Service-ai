import uuid
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status

from app.dashboard_chat.schemas import (
    DashboardChatRequest, 
    DashboardChatResponse, 
    ConversationState, 
    FiltersState,
    TicketDraftState,
    TicketUpdateDraftState,
)
from app.dashboard_chat.memory import state_store
from app.dashboard_chat.tools.project_tools import get_active_projects, get_active_categories
from app.dashboard_chat.tools.user_tools import get_active_users
from app.dashboard_chat.chains.planner_chain import run_planner_chain
from app.dashboard_chat.chains.answer_chain import (
    _performance_analysis,
    run_answer_chain,
)
from app.dashboard_chat.plan_executor import execute_plan

router = APIRouter(prefix="/api/assistant/admin", tags=["dashboard-chat"])
logger = logging.getLogger(__name__)


def _normalized_scope_value(value: str) -> str:
    return str(value or "").strip().lower()

SOURCE_LABELS = {
    "modify_ticket": "Ticket Registration",
    "create_ticket": "Ticket Registration",
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
        if value is not None
        and key not in {
            "pageIndex",
            "pageSize",
            "description",
            "descriptionOldText",
            "descriptionNewText",
            "confirm",
        }
        and not key.startswith("_")
    }
    return sources, filters


TICKET_DRAFT_FIELDS = ("projectName", "categoryName", "priority", "description")
TICKET_UPDATE_FIELDS = (
    "ticketId",
    "ticketNo",
    "projectName",
    "categoryName",
    "priority",
    "description",
    "descriptionAction",
    "descriptionOldText",
    "descriptionNewText",
)


def is_ticket_creation_cancel(message: str) -> bool:
    lower = " ".join((message or "").lower().split())
    return lower in {
        "cancel",
        "cancel ticket",
        "cancel the ticket",
        "stop",
        "stop creating ticket",
        "never mind",
        "nevermind",
    }


def is_ticket_update_cancel(message: str) -> bool:
    lower = " ".join((message or "").lower().split())
    return lower in {
        "cancel",
        "cancel update",
        "cancel modification",
        "cancel ticket update",
        "stop",
        "stop updating ticket",
        "never mind",
        "nevermind",
    }


def merge_ticket_draft(state: ConversationState, args: Dict[str, Any]) -> Dict[str, Any]:
    draft = state.ticketDraft or TicketDraftState()
    for field in TICKET_DRAFT_FIELDS:
        value = args.get(field)
        if isinstance(value, str) and value.strip():
            setattr(draft, field, value.strip())
    state.ticketDraft = draft
    return draft.model_dump(exclude_none=True)


def merge_ticket_update_draft(state: ConversationState, args: Dict[str, Any]) -> Dict[str, Any]:
    draft = state.ticketUpdateDraft or TicketUpdateDraftState()
    description_action = args.get("descriptionAction")
    if description_action == "replace_text":
        draft.description = None
    elif description_action == "replace_all":
        draft.descriptionOldText = None
        draft.descriptionNewText = None
    for field in TICKET_UPDATE_FIELDS:
        value = args.get(field)
        if isinstance(value, str) and value.strip():
            setattr(draft, field, value.strip())
    state.ticketUpdateDraft = draft
    return {
        **draft.model_dump(exclude_none=True),
        "confirm": args.get("confirm") is True,
    }


def format_ticket_created(data: Dict[str, Any]) -> Dict[str, str]:
    ticket_id = data.get("ticketId")
    ticket_no = data.get("ticketNo")
    identifiers = []
    if ticket_id:
        identifiers.append(f"Ticket ID: **{ticket_id}**")
    if ticket_no:
        identifiers.append(f"Ticket Number: **{ticket_no}**")
    identifier_text = "\n" + "\n".join(f"- {item}" for item in identifiers) if identifiers else ""
    return {
        "message": (
            f"Ticket was raised successfully for "
            f"**{data.get('projectName')}** in **{data.get('categoryName')}** "
            f"with **{data.get('priority')}** priority."
            f"{identifier_text}"
        )
    }


def format_ticket_updated(data: Dict[str, Any]) -> Dict[str, str]:
    fields = ", ".join(data.get("changedFields") or [])
    return {
        "message": (
            "Ticket was updated successfully.\n"
            f"- Ticket Number: **{data.get('ticketNo')}**\n"
            f"- Modified fields: **{fields}**"
        )
    }


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
    analysis = _performance_analysis(data)
    assigned = analysis["assigned"]
    created = analysis["created"]
    comparison = analysis["volumeComparison"]
    period = data.get("period") or {}
    assigned_total = assigned["total"]
    created_total = created["total"]
    start_date = period.get("startDate")
    end_date = period.get("endDate")
    employee_name = data.get("employeeName") or "Employee"
    period_text = (
        f"from **{start_date} to {end_date}**"
        if period.get("appliedToData") is True and start_date and end_date
        else "in the available data"
    )

    def percentage(value: Any) -> str:
        if value is None:
            return "unavailable"
        return f"{float(value):.1f}".rstrip("0").rstrip(".") + "%"

    if not assigned_total and not created_total:
        return {
            "message": (
                f"### {employee_name}\n"
                f"- **Conclusion:** No assigned or created ticket activity was recorded "
                f"{period_text}, so there is no workload evidence to analyze."
            ),
        }

    insights = []
    if assigned_total:
        active_rate = assigned["activeRatePct"] or 0
        backlog_label = "minimal" if active_rate <= 5 else "limited" if active_rate <= 20 else "material"
        insights.append(
            f"**Resolution:** {period_text.capitalize()}, **{percentage(assigned['resolutionRatePct'])}** of assigned workload was resolved "
            f"(**{assigned['resolved']} of {assigned_total}** tickets). The active backlog is "
            f"**{percentage(assigned['activeRatePct'])}** (**{assigned['active']}** tickets), which is {backlog_label}."
        )
        if assigned["overdue"]:
            risk_text = (
                f"**Risk:** **{percentage(assigned['overdueRatePct'])}** of assigned workload is overdue "
                f"(**{assigned['overdue']}** tickets), making overdue follow-up the immediate priority."
            )
        else:
            risk_text = "**Risk:** No assigned tickets are overdue, so there is no recorded overdue exposure in this period."
        if assigned["pendingOnServer"] and assigned["active"]:
            risk_text += (
                f" Server-pending work represents **{percentage(assigned['pendingServerShareOfActivePct'])}** "
                f"of the active backlog (**{assigned['pendingOnServer']} of {assigned['active']}** tickets)."
            )
        insights.append(risk_text)
    else:
        insights.extend([
            f"**Resolution:** No assigned tickets were recorded {period_text}.",
            "**Risk:** With no assigned workload, assigned-ticket backlog and overdue exposure are not applicable.",
        ])

    relationship = comparison["createdComparedWithAssigned"]
    difference = comparison["absoluteDifference"]
    if relationship == "matched":
        comparison_text = f"created volume matched assigned volume at **{created_total}** tickets each"
    else:
        comparison_text = (
            f"created volume was **{difference}** tickets {relationship} than assigned volume "
            f"(**{created_total} created vs {assigned_total} assigned**)"
        )
    if created_total:
        insights.append(
            f"**Volume context:** {comparison_text.capitalize()}, while **{percentage(created['resolutionRatePct'])}** of created tickets "
            f"were resolved. These populations may overlap, so this comparison does not prove self-assignment."
        )
    else:
        insights.append("**Volume context:** No tickets were created by the employee during the selected period.")

    if assigned_total and assigned["overdue"]:
        conclusion = "Resolution progress is offset by overdue exposure; overdue tickets should be reviewed first."
    elif assigned_total and (assigned["activeRatePct"] or 0) <= 5:
        focus = (
            "the server-pending item" if assigned["pendingOnServer"] == 1
            else "server-pending items" if assigned["pendingOnServer"] > 1
            else "the small remaining active queue"
        )
        conclusion = f"The assigned workload is highly resolved with low backlog risk; follow-up can focus on {focus}."
    elif assigned_total:
        conclusion = "The workload shows meaningful unresolved volume; reducing the active queue is the clearest follow-up priority."
    else:
        conclusion = "Only created-ticket activity is available, so no conclusion can be drawn about assigned workload."
    insights.append(f"**Conclusion:** {conclusion}")
    return {
        "message": f"### {employee_name}\n" + "\n".join(f"- {insight}" for insight in insights[:4]),
    }


def format_employee_workload(data: Dict[str, Any]) -> Dict[str, Any]:
    total = int(data.get("totalAssigned") or 0)
    in_process = int(data.get("inProcess") or 0)
    reopened = int(data.get("reopened") or 0)
    overdue = int(data.get("overdue") or 0)
    completed = int(data.get("completed") or 0)
    closed = int(data.get("closed") or 0)
    active = in_process + reopened + overdue
    resolved = completed + closed
    as_of_date = data.get("asOfDate") or "today"

    if not total:
        return {
            "message": (
                f"### Today's workload — {as_of_date}\n"
                "- No assigned tickets were found in the current workload snapshot."
            ),
        }

    active_rate = round(active * 100 / total, 1)
    resolved_rate = round(resolved * 100 / total, 1)
    conclusion = (
        "There is no reopened or overdue backlog; follow-up is concentrated on the in-process queue."
        if not reopened and not overdue
        else "Reopened and overdue tickets should be prioritized within the active queue."
    )
    return {
        "message": (
            f"### Today's workload — {as_of_date}\n"
            f"- **Current assigned snapshot:** **{total}** tickets across the tracked workload statuses.\n"
            f"- **Needs attention:** **{active}** tickets (**{active_rate}%**) — "
            f"{in_process} in process or pending on server, {reopened} reopened, and {overdue} overdue.\n"
            f"- **Resolved status:** **{resolved}** tickets (**{resolved_rate}%**) — "
            f"{completed} completed and {closed} closed.\n"
            f"- **Conclusion:** {conclusion}"
        ),
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


def format_ticket_list(data: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    total = int(data.get("totalCount") or 0)
    returned = len(data.get("tickets") or [])
    status_value = args.get("status")
    label = f" with status **{status_value}**" if status_value else ""
    if not total:
        return {"message": f"I found no tickets{label} matching those filters."}
    page = int(data.get("pageIndex") or 1)
    showing = f" Showing **{returned}** on page **{page}**." if returned < total else ""
    return {"message": f"I found **{total}** tickets{label}.{showing}"}

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

async def handle_dashboard_chat(
    request: DashboardChatRequest,
    *,
    employee_scope: bool = False,
):
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
            blockId=request.blockId,
            accessRole="Employee" if employee_scope else "Admin",
        )
    elif (
        _normalized_scope_value(state.userId),
        _normalized_scope_value(state.entityId),
        _normalized_scope_value(state.blockId),
        state.accessRole.lower(),
    ) != (
        _normalized_scope_value(request.userId),
        _normalized_scope_value(request.entityId),
        _normalized_scope_value(request.blockId),
        "employee" if employee_scope else "admin",
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Conversation does not belong to this user scope.")

    if state.ticketUpdateDraft and is_ticket_update_cancel(request.message):
        message = "Ticket modification cancelled."
        state.ticketUpdateDraft = None
        state_store.add_turn(state, "user", request.message)
        state_store.add_turn(state, "assistant", message)
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            traceId=trace_id,
            type="answer",
            message=message,
            sources=["Conversation"],
        )

    if state.ticketDraft and is_ticket_creation_cancel(request.message):
        message = "Ticket creation cancelled."
        state.ticketDraft = None
        state_store.add_turn(state, "user", request.message)
        state_store.add_turn(state, "assistant", message)
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            traceId=trace_id,
            type="answer",
            message=message,
            sources=["Conversation"],
        )

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
        "ticketDraft": state.ticketDraft.model_dump(exclude_none=True) if state.ticketDraft else None,
        "ticketUpdateDraft": (
            state.ticketUpdateDraft.model_dump(exclude_none=True)
            if state.ticketUpdateDraft
            else None
        ),
    }

    # 3. Ask the LLM for a dynamic tool plan instead of a fixed intent.
    plan = await run_planner_chain(
        request.message,
        prev_state_dict,
        employee_scope=employee_scope,
    )

    update_call = next(
        (call for call in (plan.get("tool_calls") or []) if call.get("tool") == "modify_ticket"),
        None,
    )
    if update_call is not None:
        update_call["args"] = merge_ticket_update_draft(state, update_call.get("args") or {})
        state.ticketDraft = None
        state_store.set(conversation_id, state)

    creation_call = next(
        (call for call in (plan.get("tool_calls") or []) if call.get("tool") == "create_ticket"),
        None,
    )
    if creation_call is not None:
        creation_call["args"] = merge_ticket_draft(state, creation_call.get("args") or {})
        state.ticketUpdateDraft = None
        state_store.set(conversation_id, state)

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
            employee_scope=employee_scope,
        )
    except Exception:
        logger.exception("Dashboard chat tool execution failed; trace=%s", trace_id)
        failure_message = "I could not complete that request right now. Please try again."
        state_store.add_turn(state, "user", request.message)
        state_store.add_turn(state, "assistant", failure_message)
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            traceId=trace_id,
            type="answer",
            message=failure_message,
            # suggestions=["Retry"]
        )

    for result in tool_results:
        if result.get("type") == "clarification":
            if result.get("tool") == "modify_ticket":
                merge_ticket_update_draft(state, result.get("args") or {})
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

    failed_result = next(
        (
            result for result in reversed(tool_results)
            if result.get("type") in {"error", "access_denied"}
        ),
        None,
    )
    if failed_result:
        failure_message = failed_result.get("message", "I could not complete that request.")
        state_store.add_turn(state, "user", request.message)
        state_store.add_turn(state, "assistant", failure_message)
        state_store.set(conversation_id, state)
        return DashboardChatResponse(
            conversationId=conversation_id,
            traceId=trace_id,
            type="answer",
            message=failure_message,
            # suggestions=failed_result.get("suggestions") or ["Try again"],
        )

    final_result = next((r for r in reversed(tool_results) if r.get("type") == "tool_result"), tool_results[-1])
    kind = final_result.get("kind", plan.get("response_format") or "summary")
    tool_data = final_result.get("data")

    # 4. Summarize the executed tool data. Grouped counts are deterministic to avoid LLM count drift.
    if kind == "grouped_ticket_summary" and isinstance(tool_data, dict):
        answer_res = format_grouped_summary(tool_data)
    elif kind == "ticket_updated" and isinstance(tool_data, dict):
        answer_res = format_ticket_updated(tool_data)
    elif kind == "ticket_created" and isinstance(tool_data, dict):
        answer_res = format_ticket_created(tool_data)
    elif kind == "monthly_ticket_count" and isinstance(tool_data, dict):
        answer_res = format_monthly_ticket_count(tool_data)
    elif kind == "employee_workload" and isinstance(tool_data, dict):
        answer_res = format_employee_workload(tool_data)
    elif kind == "employee_performance" and isinstance(tool_data, dict):
        answer_res = format_employee_performance(tool_data)
    elif kind == "ticket_list" and isinstance(tool_data, dict):
        answer_res = (
            format_ticket_count(tool_data, final_result.get("args") or {})
            if is_count_only_query(request.message)
            else format_ticket_list(tool_data, final_result.get("args") or {})
        )
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
    if kind == "ticket_created":
        state.ticketDraft = None
        state.lastTool = None
        state.lastArgs = {}
    elif kind == "ticket_updated":
        state.ticketUpdateDraft = None
        state.lastTool = None
        state.lastArgs = {}
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


@router.post("/dashboard-chat", response_model=DashboardChatResponse)
async def dashboard_chat(request: DashboardChatRequest):
    if _normalized_scope_value(request.userRole) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin chatbot access requires an Admin role.",
        )
    return await handle_dashboard_chat(request)
