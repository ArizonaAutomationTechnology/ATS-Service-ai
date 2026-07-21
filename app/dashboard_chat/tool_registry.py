import json
import calendar
import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.config import settings
from app.dashboard_chat.services.date_parser import parse_date_range
from app.dashboard_chat.tools.dashboard_tools import get_dashboard_counts, get_priority_counts, get_ticket_count_month_wise
from app.dashboard_chat.tools.project_tools import get_active_categories, get_active_projects, get_project_ticket_counts
from app.dashboard_chat.tools.ticket_tools import get_ticket_history, get_tickets
from app.dashboard_chat.tools.user_tools import get_active_users, get_user_workloads


ToolHandler = Callable[..., Awaitable[Any]]


TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "search_tickets",
        "description": "Find paginated tickets by status, date range, project, category, user, priority, ticket number, or keyword.",
        "args": ["status", "dateRangeText", "startDate", "endDate", "priority", "projectName", "categoryName", "userName", "projectId", "categoryId", "assignedTo", "ticketNo", "keyword", "pageIndex", "pageSize"],
        "returns": "ticket_list",
        "guardrails": "For broad searches, requires a dateRangeText/startDate/endDate or one narrow filter such as ticketNo, projectName, userName, or categoryName. Max pageSize is 30.",
    },
    {
        "name": "get_ticket_history",
        "description": "Fetch tracker/history comments for one specific ticket.",
        "args": ["ticketId", "ticketNo"],
        "returns": "ticket_history",
        "guardrails": "Requires ticketId. If only ticketNo is known, first use search_tickets with ticketNo.",
    },
    {
        "name": "get_dashboard_counts",
        "description": "Fetch dashboard status totals such as all, raised, in-process, completed, closed, reopened, overdue, and rejected ticket counts.",
        "args": [],
        "returns": "counts",
    },
    {
        "name": "get_priority_counts",
        "description": "Fetch high, medium, and low priority ticket counts.",
        "args": [],
        "returns": "counts",
    },
    {
        "name": "get_project_ticket_counts",
        "description": "Fetch project-wise total, active, and pending-on-server ticket counts for project health or project bottleneck questions.",
        "args": ["projectName", "projectId"],
        "returns": "project_health",
    },
    {
        "name": "get_user_workloads",
        "description": "Fetch user/employee workload counts for overloaded user or bottleneck questions.",
        "args": ["userName", "assignedTo"],
        "returns": "user_workload",
    },
    {
        "name": "get_active_projects",
        "description": "List active projects, mainly for resolving project names.",
        "args": ["projectName"],
        "returns": "lookup_list",
    },
    {
        "name": "get_active_users",
        "description": "List active users/employees, mainly for resolving user names.",
        "args": ["userName"],
        "returns": "lookup_list",
    },
    {
        "name": "get_active_categories",
        "description": "List active ticket categories, mainly for resolving category names.",
        "args": ["categoryName"],
        "returns": "lookup_list",
    },
    {
        "name": "group_tickets",
        "description": "Group the previous or current ticket list by projectName, categoryName, assignedToName, priority, or status.",
        "args": ["groupBy"],
        "returns": "grouped_ticket_summary",
        "guardrails": "Requires a previous search_tickets result in the same plan or conversation.",
    },
]


def get_tool_catalog_for_prompt() -> str:
    return json.dumps(TOOL_CATALOG, indent=2)


def _norm(value: Optional[str]) -> str:
    return (value or "").lower().replace("project", "").strip()


def _contains_name(candidate: Dict[str, Any], query: Optional[str], keys: List[str]) -> bool:
    q = _norm(query)
    if not q:
        return False
    for key in keys:
        value = _norm(str(candidate.get(key) or ""))
        if q and q in value:
            return True
    return False


async def resolve_lookup_ids(entity_id: str, block_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(args)

    if args.get("projectName") and not args.get("projectId"):
        projects = await get_active_projects(entity_id, block_id)
        for project in projects:
            if _contains_name(project, args.get("projectName"), ["name", "projectName"]):
                resolved["projectId"] = project.get("id") or project.get("projectId")
                break

    if args.get("userName") and not args.get("assignedTo"):
        users = await get_active_users(entity_id, block_id)
        for user in users:
            if _contains_name(user, args.get("userName"), ["name", "userName", "empId"]):
                resolved["assignedTo"] = user.get("id") or user.get("userId")
                break

    if args.get("categoryName") and not args.get("categoryId"):
        categories = await get_active_categories(entity_id, block_id)
        for category in categories:
            if _contains_name(category, args.get("categoryName"), ["name", "categoryName"]):
                resolved["categoryId"] = category.get("id") or category.get("categoryId")
                break

    return resolved


def _has_narrow_filter(args: Dict[str, Any]) -> bool:
    return any(args.get(key) for key in ["status", "ticketNo", "projectName", "projectId", "userName", "assignedTo", "categoryName", "categoryId", "keyword"])


def _normalize_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    value = status.strip().lower()
    mapping = {
        "overdue": "Overdue",
        "reopen": "Re Open",
        "re-open": "Re Open",
        "reopened": "Re Open",
        "re open": "Re Open",
        "closed": "Closed",
        "completed": "Completed",
        "complete": "Completed",
        "in process": "In Process",
        "inprocess": "In Process",
        "raised": "Raised",
        "rejected": "Rejected",
        "reject": "Rejected",
    }
    return mapping.get(value, status)


def _normalize_priority(priority: Optional[str]) -> Optional[str]:
    if not priority:
        return None
    value = priority.strip().lower()
    if value in {"high", "medium", "low"}:
        return value.title()
    return priority


def _normalize_group_by(group_by: Optional[str]) -> str:
    value = (group_by or "projectName").strip()
    aliases = {
        "assignee": "assignedToName",
        "assignedTo": "assignedToName",
        "assignedToTask": "assignedToName",
        "user": "assignedToName",
        "employee": "assignedToName",
        "owner": "assignedToName",
        "project": "projectName",
        "category": "categoryName",
        "priority": "priority",
        "status": "status",
    }
    return aliases.get(value, aliases.get(value.lower(), value))


def _extract_single_month(args: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    start_date = args.get("startDate")
    end_date = args.get("endDate")
    date_range_text = args.get("dateRangeText")
    if date_range_text and not (start_date and end_date):
        start_date, end_date = parse_date_range(date_range_text)

    if not start_date:
        return None, None

    try:
        start = datetime.date.fromisoformat(str(start_date)[:10])
        end = datetime.date.fromisoformat(str(end_date)[:10]) if end_date else start
    except ValueError:
        return None, None

    if start.year != end.year or start.month != end.month:
        return None, None

    return calendar.month_name[start.month], str(start.year)


def _select_monthly_volume(data: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    month, year = _extract_single_month(args)
    if not month or not year:
        return {
            "month": month,
            "year": year,
            "count": None,
            "monthlyData": data,
        }

    year_data = data.get(year) if isinstance(data, dict) else {}
    count = year_data.get(month, 0) if isinstance(year_data, dict) else 0
    return {
        "month": month,
        "year": year,
        "count": count,
        "monthlyData": data,
    }


async def _fetch_complete_ticket_rows_for_grouping(
    *,
    entity_id: str,
    block_id: str,
    user_id: str,
    source_result: Dict[str, Any],
) -> Dict[str, Any]:
    source_data = source_result.get("data") or {}
    query_args = source_result.get("args") or {}
    complete_result = await get_tickets(
        entity_id=entity_id,
        block_id=block_id,
        user_id=user_id,
        status=_normalize_status(query_args.get("status")),
        start_date=query_args.get("startDate"),
        end_date=query_args.get("endDate"),
        priority=_normalize_priority(query_args.get("priority")),
        project_id=query_args.get("projectId"),
        category_id=query_args.get("categoryId"),
        assigned_to=query_args.get("assignedTo"),
        keyword=query_args.get("keyword"),
        ticket_no=query_args.get("ticketNo"),
        for_report=True,
    )
    tickets = complete_result.get("tickets") or []
    total_count = int(complete_result.get("totalCount") or len(tickets))
    ticket_ids = {ticket.get("ticketId") for ticket in tickets if ticket.get("ticketId")}
    is_complete = len(tickets) == total_count and len(ticket_ids) == total_count

    return {
        "tickets": tickets,
        "totalCount": total_count,
        "groupedRowCount": len(tickets),
        "isComplete": is_complete,
    }


async def execute_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
    previous_result: Any = None,
) -> Dict[str, Any]:
    args = dict(args or {})
    page_size = min(int(args.get("pageSize") or settings.DASHBOARD_CHAT_MAX_PAGE_SIZE), settings.DASHBOARD_CHAT_MAX_PAGE_SIZE)
    page_index = max(1, int(args.get("pageIndex") or 1))

    if tool_name == "search_tickets":
        args = await resolve_lookup_ids(entity_id, block_id, args)
        start_date = args.get("startDate")
        end_date = args.get("endDate")
        date_range_text = args.get("dateRangeText")
        if date_range_text and not (start_date and end_date):
            start_date, end_date = parse_date_range(date_range_text)

        if not (start_date and end_date) and not _has_narrow_filter(args):
            return {
                "type": "clarification",
                "message": "Which date range should I use for this ticket search?",
                "suggestions": ["This month", "Last month", "This week", "Today"],
            }

        result = await get_tickets(
            entity_id=entity_id,
            block_id=block_id,
            user_id=user_id,
            status=_normalize_status(args.get("status")),
            start_date=start_date,
            end_date=end_date,
            priority=_normalize_priority(args.get("priority")),
            project_id=args.get("projectId"),
            category_id=args.get("categoryId"),
            assigned_to=args.get("assignedTo"),
            keyword=args.get("keyword"),
            ticket_no=args.get("ticketNo"),
            page_index=page_index,
            page_size=page_size,
        )
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "ticket_list",
            "args": {**args, "startDate": start_date, "endDate": end_date, "pageIndex": page_index, "pageSize": page_size},
            "data": result,
        }

    if tool_name == "get_ticket_history":
        ticket_id = args.get("ticketId")
        if not ticket_id:
            return {
                "type": "clarification",
                "message": "Please provide the ticket number or open a specific ticket first.",
                "suggestions": ["Search by ticket number", "Show overdue tickets"],
            }
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "ticket_history",
            "args": args,
            "data": await get_ticket_history(ticket_id),
        }

    if tool_name == "get_dashboard_counts":
        if args.get("metric") == "monthly_volume":
            monthly_data = await get_ticket_count_month_wise()
            return {
                "type": "tool_result",
                "tool": tool_name,
                "kind": "monthly_ticket_count",
                "args": args,
                "data": _select_monthly_volume(monthly_data, args),
            }
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "counts",
            "args": args,
            "data": await get_dashboard_counts(user_id, entity_id, block_id),
        }

    if tool_name == "get_priority_counts":
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "counts",
            "args": args,
            "data": await get_priority_counts(),
        }

    if tool_name == "get_project_ticket_counts":
        projects = await get_project_ticket_counts()
        project_name = args.get("projectName")
        project_id = args.get("projectId")
        if project_name:
            projects = [p for p in projects if _contains_name(p, project_name, ["name", "projectName"])]
        if project_id:
            projects = [p for p in projects if str(p.get("id") or p.get("Id") or p.get("projectId")) == str(project_id)]
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "project_health",
            "args": args,
            "data": {"projects": projects[:30]},
        }

    if tool_name == "get_user_workloads":
        data = await get_user_workloads()
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "user_workload",
            "args": args,
            "data": data,
        }

    if tool_name == "get_active_projects":
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "lookup_list",
            "args": args,
            "data": await get_active_projects(entity_id, block_id),
        }

    if tool_name == "get_active_users":
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "lookup_list",
            "args": args,
            "data": await get_active_users(entity_id, block_id),
        }

    if tool_name == "get_active_categories":
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "lookup_list",
            "args": args,
            "data": await get_active_categories(entity_id, block_id),
        }

    if tool_name == "group_tickets":
        group_by = _normalize_group_by(args.get("groupBy"))
        source = previous_result or {}
        if isinstance(source, dict) and source.get("type") == "tool_result" and source.get("kind") == "ticket_list":
            source_data = await _fetch_complete_ticket_rows_for_grouping(
                entity_id=entity_id,
                block_id=block_id,
                user_id=user_id,
                source_result=source,
            )
            tickets = source_data.get("tickets")
            total_count = source_data.get("totalCount", len(tickets or []))
            grouped_row_count = source_data.get("groupedRowCount", len(tickets or []))
            is_complete = source_data.get("isComplete", True)
        else:
            tickets = source.get("tickets") if isinstance(source, dict) else None
            total_count = len(tickets or [])
            grouped_row_count = len(tickets or [])
            is_complete = True
        if not tickets:
            return {
                "type": "clarification",
                "message": "Please search for tickets first, then I can group those results.",
                "suggestions": ["Show overdue tickets this month", "Show high priority tickets this week"],
            }
        if not is_complete:
            return {
                "type": "error",
                "message": "I could not retrieve a complete ticket set, so I will not report an inaccurate grouped count.",
                "suggestions": ["Try again", "Use a shorter date range"],
            }
        groups: Dict[str, int] = {}
        for ticket in tickets:
            key = str(ticket.get(group_by) or "Unspecified")
            groups[key] = groups.get(key, 0) + 1
        grouped = [{"group": key, "count": count} for key, count in sorted(groups.items(), key=lambda item: item[1], reverse=True)]
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "grouped_ticket_summary",
            "args": args,
            "data": {
                "groupBy": group_by,
                "groups": grouped,
                "totalCount": total_count,
                "groupedRowCount": grouped_row_count,
                "isComplete": is_complete,
            },
        }

    return {
        "type": "error",
        "message": f"Unsupported tool: {tool_name}",
        "suggestions": ["Show overdue tickets", "Project health", "User workload"],
    }
