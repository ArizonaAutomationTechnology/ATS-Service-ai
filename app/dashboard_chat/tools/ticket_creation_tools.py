from typing import Any, Dict, Iterable, Optional

from app.dashboard_chat.services.aspnet_client import aspnet_client
from app.dashboard_chat.tools.project_tools import get_active_categories, get_active_projects


REQUIRED_TICKET_FIELDS = ("categoryName", "projectName", "priority", "description")
PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
CHATBOT_TICKET_CLIENT_ID = "A0430A16-6918-4DFA-BE07-EF528F73483C"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any, *, project: bool = False) -> str:
    result = " ".join(_clean(value).lower().split())
    return result.removesuffix(" project").strip() if project else result


def _row_value(row: Dict[str, Any], keys: Iterable[str]) -> Optional[Any]:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _resolve_named_row(
    rows: list[Dict[str, Any]],
    query: str,
    *,
    name_keys: tuple[str, ...],
    id_keys: tuple[str, ...],
    project: bool = False,
) -> tuple[Optional[str], Optional[str], list[str]]:
    query_value = _normalized(query, project=project)
    exact_matches = []
    partial_matches = []
    for row in rows:
        name = _row_value(row, name_keys)
        row_id = _row_value(row, id_keys)
        if not name or not row_id:
            continue
        normalized_name = _normalized(name, project=project)
        if query_value == normalized_name:
            exact_matches.append((str(row_id), str(name)))
        elif query_value and query_value in normalized_name:
            partial_matches.append((str(row_id), str(name)))

    matches = exact_matches or partial_matches
    if len(matches) == 1:
        return matches[0][0], matches[0][1], []
    return None, None, [name for _, name in matches[:5]]


def missing_ticket_fields(args: Dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_TICKET_FIELDS if not _clean(args.get(field))]


def missing_ticket_message(missing: list[str]) -> str:
    labels = {
        "projectName": "**Project**",
        "categoryName": "**Category**",
        "priority": "**Priority Level** (High, Medium, or Low)",
        "description": "**Ticket Description**",
    }
    requested = [labels[field] for field in missing]
    if len(requested) == 1:
        fields_text = requested[0]
    else:
        fields_text = ", ".join(requested[:-1]) + f", and {requested[-1]}"
    return f"Please provide {fields_text} to raise the ticket."


async def create_ticket(
    args: Dict[str, Any],
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
) -> Dict[str, Any]:
    draft = {field: _clean(args.get(field)) for field in REQUIRED_TICKET_FIELDS}
    missing = missing_ticket_fields(draft)
    if missing:
        return {
            "type": "clarification",
            "tool": "create_ticket",
            "kind": "ticket_draft",
            "args": draft,
            "message": missing_ticket_message(missing),
        }

    priority = PRIORITIES.get(draft["priority"].lower())
    if not priority:
        return {
            "type": "clarification",
            "tool": "create_ticket",
            "kind": "ticket_draft",
            "args": draft,
            "message": "Please provide a valid **Priority Level**: High, Medium, or Low.",
        }
    draft["priority"] = priority

    projects = await get_active_projects(entity_id, block_id)
    project_id, project_name, project_matches = _resolve_named_row(
        projects,
        draft["projectName"],
        name_keys=("name", "Name", "projectName", "ProjectName"),
        id_keys=("id", "Id", "projectId", "ProjectId"),
        project=True,
    )
    if not project_id:
        suffix = f" Matching projects: {', '.join(project_matches)}." if project_matches else ""
        return {
            "type": "clarification",
            "tool": "create_ticket",
            "kind": "ticket_draft",
            "args": draft,
            "message": f"I could not identify one active project named **{draft['projectName']}**.{suffix}",
        }

    categories = await get_active_categories(entity_id, block_id)
    category_id, category_name, category_matches = _resolve_named_row(
        categories,
        draft["categoryName"],
        name_keys=("name", "Name", "categoryName", "CategoryName"),
        id_keys=("id", "Id", "categoryId", "CategoryId"),
    )
    if not category_id:
        suffix = f" Matching categories: {', '.join(category_matches)}." if category_matches else ""
        return {
            "type": "clarification",
            "tool": "create_ticket",
            "kind": "ticket_draft",
            "args": draft,
            "message": f"I could not identify one active category named **{draft['categoryName']}**.{suffix}",
        }

    description = draft["description"]
    form_data = [
        ("ProjectId", project_id),
        ("ProjectName", project_name),
        ("CategoryId", category_id),
        ("CategoryIds", category_id),
        ("CategoryNames", category_name),
        ("CategoryNameString", category_name),
        ("ClientId", CHATBOT_TICKET_CLIENT_ID),
        ("Title", "null"),
        ("Description", description),
        ("Status", "Raised"),
        ("Priority", priority),
        ("IsReOpen", "false"),
        ("CreatedBy", user_id),
        ("EntityId", entity_id),
        ("BlockId", block_id),
    ]
    response = await aspnet_client.post_form("api/TicketRegistration/Add", form_data)
    try:
        response_data = response.json()
    except ValueError:
        response_data = {}
    response_record = response_data if isinstance(response_data, dict) else {}
    for key in ("data", "result", "ticket"):
        nested = response_record.get(key)
        if isinstance(nested, dict):
            response_record = nested
            break

    return {
        "type": "tool_result",
        "tool": "create_ticket",
        "kind": "ticket_created",
        "args": draft,
        "data": {
            "ticketId": _row_value(response_record, ("id", "Id", "ticketId", "TicketId")),
            "ticketNo": _row_value(response_record, ("ticketNo", "TicketNo")),
            "projectName": project_name,
            "categoryName": category_name,
            "priority": priority,
            "status": "Raised",
        },
    }
