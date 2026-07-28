import json
import calendar
import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import ConfigDict, create_model

from app.config import settings
from app.dashboard_chat.services.date_parser import parse_date_range
from app.dashboard_chat.tools.dashboard_tools import get_dashboard_counts, get_priority_counts, get_ticket_count_month_wise
from app.dashboard_chat.tools.project_tools import get_active_categories, get_active_projects, get_project_ticket_counts
from app.dashboard_chat.tools.ticket_tools import get_ticket_history, get_tickets
from app.dashboard_chat.tools.user_tools import get_active_users, get_employee_performance, get_user_workloads
from app.dashboard_chat.tools.organization_tools import (
    get_changes,
    get_clients,
    get_departments,
    get_designations,
    get_employees,
    get_file_logs,
    get_organizations,
    get_project_documents,
)


ToolHandler = Callable[..., Awaitable[Any]]

EMPLOYEE_TOOL_NAMES = {
    "search_tickets",
    "get_ticket_history",
    "get_dashboard_counts",
    "get_employee_performance",
    "group_tickets",
}


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
        "args": ["metric", "dateRangeText", "startDate", "endDate"],
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
        "name": "get_employee_performance",
        "description": "Summarize one employee's work activity and dashboard ticket metrics for a supplied date range.",
        "args": ["userName", "dateRangeText"],
        "returns": "employee_performance",
        "guardrails": "Requires one employee and a date range. For 'this year', query from 1 January through today.",
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
    {
        "name": "list_organizations",
        "description": "List organizations available to the current user.",
        "args": ["name"],
        "returns": "organization_list",
    },
    {
        "name": "list_departments",
        "description": "List active departments or find a department by name.",
        "args": ["name"],
        "returns": "department_list",
    },
    {
        "name": "list_designations",
        "description": "List active designations or find a designation by name.",
        "args": ["name"],
        "returns": "designation_list",
    },
    {
        "name": "search_employees",
        "description": "Find active employees by name, department, or designation.",
        "args": ["name", "departmentName", "designationName"],
        "returns": "employee_list",
    },
    {
        "name": "search_projects",
        "description": "Find active projects by project or client name.",
        "args": ["name", "clientName"],
        "returns": "project_list",
    },
    {
        "name": "search_clients",
        "description": "Find CRM clients by name, contact, or email.",
        "args": ["name"],
        "returns": "client_list",
    },
    {
        "name": "search_changes",
        "description": "Find Change Tracker entries by project, client, status, or keyword.",
        "args": ["keyword", "projectName", "clientName", "status", "dateRangeText"],
        "returns": "change_list",
    },
    {
        "name": "search_documents",
        "description": "List ShareDoc folders for one resolved project. Ask for the project when it is missing or ambiguous.",
        "args": ["projectName", "projectId"],
        "returns": "document_list",
    },
    {
        "name": "get_file_log_summary",
        "description": "List ShareDoc file open/close log entries, optionally filtered by keyword.",
        "args": ["keyword"],
        "returns": "file_log_list",
    },
]


def get_tool_catalog_for_prompt() -> str:
    return json.dumps(TOOL_CATALOG, indent=2)


def get_employee_tool_catalog() -> str:
    return json.dumps(
        [tool for tool in TOOL_CATALOG if tool["name"] in EMPLOYEE_TOOL_NAMES],
        indent=2,
    )


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


def _filter_rows(rows: List[Dict[str, Any]], *terms: Optional[str]) -> List[Dict[str, Any]]:
    terms = tuple(_norm(term) for term in terms if term)
    if not terms:
        return rows[:25]
    return [
        row for row in rows
        if all(term in " ".join(str(value or "") for value in row.values()).lower() for term in terms)
    ][:25]


def _compact_row(row: Dict[str, Any], kind: str) -> Dict[str, Any]:
    if kind == "client_list":
        values = {key.lower(): value for key, value in row.items()}
        return {
            label: values[key]
            for label, key in {
                "name": "name",
                "email": "email",
                "contact": "contact",
                "address": "address",
            }.items()
            if values.get(key) not in (None, "")
        }

    allowed = {
        "id", "name", "entityname", "organizationname", "departmentname",
        "designationname", "username", "employeeid", "empid", "projectname",
        "clientname", "contactperson", "status", "createddate", "createdbyname",
        "changetrackerno", "projectid", "clientid", "foldername", "filename",
        "filepath", "action", "openedbyname", "closedbyname",
    }
    compact = {
        key: value for key, value in row.items()
        if key.lower() in allowed and value not in (None, "")
    }
    return compact or {"id": row.get("id") or row.get("Id"), "name": row.get("name") or row.get("Name")}


def _list_result(tool_name: str, kind: str, args: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    compact_rows = [_compact_row(row, kind) for row in rows[:25]]
    return {
        "type": "tool_result",
        "tool": tool_name,
        "kind": kind,
        "args": args,
        "data": {"totalCount": len(rows), "returnedCount": len(compact_rows), "records": compact_rows},
    }


async def resolve_lookup_ids(entity_id: str, block_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(args)

    if args.get("projectName") and not args.get("projectId"):
        projects = await get_active_projects(entity_id, block_id)
        matches = [project for project in projects if _contains_name(project, args.get("projectName"), ["name", "projectName"])]
        if len(matches) == 1:
            resolved["projectId"] = matches[0].get("id") or matches[0].get("projectId")
        elif len(matches) > 1:
            resolved["_projectMatches"] = [
                project.get("name") or project.get("projectName") or "Unnamed project"
                for project in matches[:5]
            ]

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
    employee_scope: bool = False,
) -> Dict[str, Any]:
    args = dict(args or {})
    if employee_scope and tool_name not in EMPLOYEE_TOOL_NAMES:
        return {
            "type": "error",
            "message": f"The requested tool is not available in employee scope: {tool_name}.",
        }
    page_size = min(int(args.get("pageSize") or settings.DASHBOARD_CHAT_MAX_PAGE_SIZE), settings.DASHBOARD_CHAT_MAX_PAGE_SIZE)
    page_index = max(1, int(args.get("pageIndex") or 1))

    if tool_name == "search_tickets":
        if employee_scope:
            args.pop("userName", None)
            args["assignedTo"] = user_id
        else:
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
                # "suggestions": ["This month", "Last month", "This week", "Today"],
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
        if employee_scope and args.get("ticketNo"):
            lookup = await get_tickets(
                entity_id=entity_id,
                block_id=block_id,
                user_id=user_id,
                assigned_to=user_id,
                ticket_no=args["ticketNo"],
                page_index=1,
                page_size=1,
            )
            tickets = lookup.get("tickets") or []
            ticket_id = tickets[0].get("ticketId") if tickets else None
        elif employee_scope and ticket_id:
            previous_data = previous_result.get("data", previous_result) if isinstance(previous_result, dict) else {}
            allowed_ids = {
                ticket.get("ticketId")
                for ticket in (previous_data.get("tickets") or [])
                if ticket.get("ticketId")
            }
            if ticket_id not in allowed_ids:
                ticket_id = None
        if not ticket_id:
            return {
                "type": "clarification",
                "message": "Please provide the ticket number or open a specific ticket first.",
                # "suggestions": ["Search by ticket number", "Show overdue tickets"],
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

    if tool_name == "get_employee_performance":
        user_name = str(args.get("userName") or "").strip()
        if not user_name and not employee_scope:
            return {
                "type": "clarification",
                "message": "Which employee's performance should I summarize?",
            }
        users = await get_active_users(entity_id, block_id)
        matches = (
            [
                user for user in users
                if str(user.get("id") or user.get("userId")) == str(user_id)
            ]
            if employee_scope
            else [user for user in users if _contains_name(user, user_name, ["name", "userName", "empId"])]
        )
        if len(matches) != 1:
            return {
                "type": "clarification",
                "message": "I could not identify one employee. Please provide the full employee name.",
            }
        start_date, end_date = parse_date_range(str(args.get("dateRangeText") or ""))
        today = datetime.date.today().isoformat()
        if not start_date or not end_date:
            return {
                "type": "clarification",
                "message": "Which period should I use for this performance summary?",
            }
        end_date = min(end_date, today)
        if start_date > end_date:
            return {
                "type": "clarification",
                "message": "That performance period is in the future. Please choose a period up to today.",
            }
        employee = matches[0]
        employee_id = employee.get("id") or employee.get("userId")
        employee_name = employee.get("name") or employee.get("userName") or user_name
        return {
            "type": "tool_result",
            "tool": tool_name,
            "kind": "employee_performance",
            "args": {**args, "userName": employee_name, "startDate": start_date, "endDate": end_date},
            "data": await get_employee_performance(employee_id, employee_name, start_date, end_date),
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
                # "suggestions": ["Show overdue tickets this month", "Show high priority tickets this week"],
            }
        if not is_complete:
            return {
                "type": "error",
                "message": "I could not retrieve a complete ticket set, so I will not report an inaccurate grouped count.",
                # "suggestions": ["Try again", "Use a shorter date range"],
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

    if tool_name == "list_organizations":
        return _list_result(tool_name, "organization_list", args, _filter_rows(await get_organizations(), args.get("name")))

    if tool_name == "list_departments":
        return _list_result(tool_name, "department_list", args, _filter_rows(await get_departments(entity_id, block_id), args.get("name")))

    if tool_name == "list_designations":
        return _list_result(tool_name, "designation_list", args, _filter_rows(await get_designations(entity_id, block_id), args.get("name")))

    if tool_name == "search_employees":
        rows = await get_employees(entity_id, block_id)
        return _list_result(tool_name, "employee_list", args, _filter_rows(rows, args.get("name"), args.get("departmentName"), args.get("designationName")))

    if tool_name == "search_projects":
        rows = await get_active_projects(entity_id, block_id)
        return _list_result(tool_name, "project_list", args, _filter_rows(rows, args.get("name"), args.get("clientName")))

    if tool_name == "search_clients":
        return _list_result(tool_name, "client_list", args, _filter_rows(await get_clients(entity_id, block_id), args.get("name")))

    if tool_name == "search_changes":
        rows = await get_changes(entity_id, block_id)
        return _list_result(tool_name, "change_list", args, _filter_rows(rows, args.get("keyword"), args.get("projectName"), args.get("clientName"), args.get("status")))

    if tool_name == "search_documents":
        args = await resolve_lookup_ids(entity_id, block_id, args)
        if args.get("_projectMatches"):
            return {
                "type": "clarification",
                "message": "I found more than one matching project. Which one do you mean?",
                # "suggestions": args["_projectMatches"],
            }
        if not args.get("projectId"):
            return {
                "type": "clarification",
                "message": "Which project should I use to find its ShareDoc folders?",
                # "suggestions": ["Provide a project name", "List active projects"],
            }
        return _list_result(tool_name, "document_list", args, await get_project_documents(args.get("projectId")))

    if tool_name == "get_file_log_summary":
        return _list_result(tool_name, "file_log_list", args, _filter_rows(await get_file_logs(entity_id, block_id), args.get("keyword")))

    return {
        "type": "error",
        "message": f"Unsupported tool: {tool_name}",
        # "suggestions": ["Show overdue tickets", "Project health", "User workload"],
    }


def build_langchain_tools(
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
    employee_scope: bool = False,
) -> List[StructuredTool]:
    """Wrap the scoped ASP.NET operations as LangChain tools for one request."""
    runtime: Dict[str, Any] = {"previous_result": None}
    integer_args = {"pageIndex", "pageSize"}
    tools: List[StructuredTool] = []

    def make_handler(tool_name: str):
        async def handler(**kwargs: Any) -> Dict[str, Any]:
            args = {key: value for key, value in kwargs.items() if value is not None}
            result = await execute_tool(
                tool_name,
                args,
                user_id=user_id,
                entity_id=entity_id,
                block_id=block_id,
                previous_result=runtime["previous_result"],
                employee_scope=employee_scope,
            )
            if result.get("type") == "tool_result":
                runtime["previous_result"] = result
            return result

        return handler

    catalog = (
        [tool for tool in TOOL_CATALOG if tool["name"] in EMPLOYEE_TOOL_NAMES]
        if employee_scope
        else TOOL_CATALOG
    )
    for spec in catalog:
        fields = {
            arg: ((int | None) if arg in integer_args else (str | None), None)
            for arg in spec.get("args", [])
        }
        args_schema = create_model(
            f"{spec['name'].title().replace('_', '')}Args",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
        description = spec["description"]
        if spec.get("guardrails"):
            description = f"{description} Guardrails: {spec['guardrails']}"
        tools.append(StructuredTool.from_function(
            coroutine=make_handler(spec["name"]),
            name=spec["name"],
            description=description,
            args_schema=args_schema,
            infer_schema=False,
        ))

    return tools
