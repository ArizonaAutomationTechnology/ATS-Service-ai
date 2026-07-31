import logging
import re
from typing import Any, Dict, Optional

import httpx

from app.dashboard_chat.services.aspnet_client import aspnet_client
from app.dashboard_chat.tools.project_tools import get_active_categories, get_active_projects
from app.dashboard_chat.tools.ticket_creation_tools import (
    PRIORITIES,
    _clean,
    _resolve_named_row,
    _row_value,
)
from app.dashboard_chat.tools.ticket_tools import get_tickets


EDITABLE_FIELDS = ("projectName", "categoryName", "priority", "description")
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
UPDATE_FORM_FIELDS = (
    ("Id", "id"),
    ("ProjectId", "projectId"),
    ("CategoryIds", "categoryIds"),
    ("ClientId", "clientId"),
    ("Title", "title"),
    ("Description", "description"),
    ("Priority", "priority"),
)
logger = logging.getLogger(__name__)


def _same_scope(value: Any, expected: str) -> bool:
    return bool(value) and str(value).strip().lower() == str(expected).strip().lower()


def _record_value(record: Dict[str, Any], *keys: str) -> Any:
    return next((record[key] for key in keys if record.get(key) is not None), None)


def _display(value: Any) -> str:
    text = _clean(value)
    return text[:117] + "..." if len(text) > 120 else text


def _form_data(ticket: Dict[str, Any]) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    for form_name, record_name in UPDATE_FORM_FIELDS:
        value = ticket.get(record_name)
        if value is None or isinstance(value, dict):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None or isinstance(item, dict):
                continue
            if isinstance(item, bool):
                item = str(item).lower()
            parts.append((form_name, str(item)))
    return parts


async def _get_ticket_record(ticket_id: str) -> Optional[Dict[str, Any]]:
    response = await aspnet_client.get(f"api/TicketRegistration/GetTicketRegData/{ticket_id}")
    data = response.json()
    if not isinstance(data, dict):
        return None
    ticket = data.get("ticketData")
    return ticket if isinstance(ticket, dict) else None


async def _resolve_ticket_id(
    args: Dict[str, Any],
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    ticket_id = _clean(args.get("ticketId"))
    if not ticket_id:
        ticket_no = _clean(args.get("ticketNo"))
        if not ticket_no:
            return None, {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": "Please provide the **Ticket ID** or **Ticket Number** to modify.",
            }
        result = await get_tickets(
            entity_id=entity_id,
            block_id=block_id,
            user_id=user_id,
            ticket_no=ticket_no,
            page_index=1,
            page_size=10,
        )
        matches = [
            ticket for ticket in (result.get("tickets") or [])
            if _clean(ticket.get("ticketNo")).lower() == ticket_no.lower()
        ]
        if len(matches) != 1:
            return None, {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": f"I could not identify one ticket numbered **{ticket_no}**.",
            }
        ticket_id = _clean(matches[0].get("ticketId"))
        args["ticketId"] = ticket_id
    return ticket_id, None


async def modify_ticket(
    args: Dict[str, Any],
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
) -> Dict[str, Any]:
    args = dict(args or {})
    ticket_id, error = await _resolve_ticket_id(
        args,
        user_id=user_id,
        entity_id=entity_id,
        block_id=block_id,
    )
    if error:
        return error

    current = await _get_ticket_record(ticket_id)
    if not current:
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "I could not find that ticket.",
        }

    current_entity_id = _record_value(current, "entityId", "EntityId")
    current_block_id = _record_value(current, "blockId", "BlockId")
    if (
        current_entity_id and not _same_scope(current_entity_id, entity_id)
    ) or (
        current_block_id and not _same_scope(current_block_id, block_id)
    ):
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "I could not find that ticket in the current organization scope.",
        }

    created_by = _record_value(current, "createdBy", "CreatedBy")
    if not _same_scope(created_by, user_id):
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "You can only modify tickets that you created.",
        }

    ticket_status = _clean(_record_value(current, "status", "Status"))
    if ticket_status.lower() != "raised":
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": (
                "Only tickets with **Raised** status can be modified. "
                f"This ticket is currently **{ticket_status or 'Unknown'}**."
            ),
        }

    proposed = {field: _clean(args.get(field)) for field in EDITABLE_FIELDS}
    description_action = _clean(args.get("descriptionAction")).lower()
    description_old_text = _clean(args.get("descriptionOldText"))
    description_new_text = _clean(args.get("descriptionNewText"))
    if proposed["description"] and not (description_old_text or description_new_text):
        description_action = "replace_all"
    elif not description_action and (description_old_text or description_new_text):
        description_action = "replace_text"
    if description_action not in {"", "replace_all", "replace_text"}:
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "Should I replace the complete description or edit specific text within it?",
        }
    if description_action == "replace_text" and proposed["description"]:
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "Please specify either a complete description replacement or one targeted text edit.",
        }
    if description_action == "replace_text" and not (
        description_old_text and description_new_text
    ):
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "Which existing description text should I replace, and what should replace it?",
        }
    if description_action == "replace_all" and not proposed["description"]:
        proposed["description"] = description_new_text

    if not any(proposed.values()) and description_action != "replace_text":
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": (
                "What should I change? You can modify **Project**, **Category**, "
                "**Priority Level**, or **Ticket Description**."
            ),
        }

    updated = dict(current)
    changes = []

    if proposed["projectName"]:
        project_id, project_name, matches = _resolve_named_row(
            await get_active_projects(entity_id, block_id),
            proposed["projectName"],
            name_keys=("name", "Name", "projectName", "ProjectName"),
            id_keys=("id", "Id", "projectId", "ProjectId"),
            project=True,
        )
        if not project_id:
            suffix = f" Matching projects: {', '.join(matches)}." if matches else ""
            return {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": f"I could not identify one active project named **{proposed['projectName']}**.{suffix}",
            }
        if _clean(current.get("projectId")).lower() != project_id.lower():
            changes.append(("Project", current.get("projectName"), project_name))
            updated["projectId"] = project_id
            updated["projectName"] = project_name

    if proposed["categoryName"]:
        category_id, category_name, matches = _resolve_named_row(
            await get_active_categories(entity_id, block_id),
            proposed["categoryName"],
            name_keys=("name", "Name", "categoryName", "CategoryName"),
            id_keys=("id", "Id", "categoryId", "CategoryId"),
        )
        if not category_id:
            suffix = f" Matching categories: {', '.join(matches)}." if matches else ""
            return {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": f"I could not identify one active category named **{proposed['categoryName']}**.{suffix}",
            }
        current_category_ids = [
            str(value).lower() for value in (current.get("categoryIds") or [])
        ]
        if category_id.lower() not in current_category_ids or len(current_category_ids) != 1:
            changes.append(("Category", current.get("categoryNameString"), category_name))
            updated["categoryId"] = category_id
            updated["categoryIds"] = [category_id]
            updated["categoryNames"] = [category_name]
            updated["categoryNameString"] = category_name

    if proposed["priority"]:
        priority = PRIORITIES.get(proposed["priority"].lower())
        if not priority:
            return {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": "Please provide a valid **Priority Level**: High, Medium, or Low.",
            }
        if _clean(current.get("priority")).lower() != priority.lower():
            changes.append(("Priority", current.get("priority"), priority))
            updated["priority"] = priority

    current_description = _clean(current.get("description"))
    proposed_description = proposed["description"]
    if description_action == "replace_text":
        matches = list(re.finditer(
            re.escape(description_old_text),
            current_description,
            re.IGNORECASE,
        ))
        if not matches:
            return {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": (
                    f'I could not find **"{description_old_text}"** in the current '
                    "ticket description. Please specify the exact text to replace."
                ),
            }
        if len(matches) > 1:
            return {
                "type": "clarification",
                "tool": "modify_ticket",
                "kind": "ticket_update_draft",
                "args": args,
                "message": (
                    f'I found **"{description_old_text}"** more than once in the '
                    "ticket description. Please provide a longer, unique phrase."
                ),
            }
        match = matches[0]
        proposed_description = (
            current_description[:match.start()]
            + description_new_text
            + current_description[match.end():]
        )

    if proposed_description and current_description != proposed_description:
        changes.append(("Description", current.get("description"), proposed_description))
        updated["description"] = proposed_description

    if not changes:
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": "The ticket already has those values. Please provide a different change.",
        }

    if args.get("confirm") is not True:
        lines = [
            f"- **{field}**: {_display(before) or 'Not set'} → {_display(after)}"
            for field, before, after in changes
        ]
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_confirmation",
            "args": args,
            "message": (
                f"Please confirm these changes for ticket **{current.get('ticketNo')}** \n"
                + "\n".join(lines)
                + "\n\nReply **confirm** to apply them."
            ),
        }

    category_ids = [
        _clean(value)
        for value in (updated.get("categoryIds") or [])
        if _clean(value) and _clean(value).lower() != EMPTY_GUID
    ]
    category_id = _clean(updated.get("categoryId"))
    if (
        category_id
        and category_id.lower() != EMPTY_GUID
        and category_id.lower() not in {value.lower() for value in category_ids}
    ):
        category_ids.insert(0, category_id)

    update_payload = {
        "id": _record_value(current, "id", "Id"),
        "projectId": updated.get("projectId"),
        "categoryIds": category_ids,
        "clientId": updated.get("clientId"),
        "title": "null",
        "description": updated.get("description"),
        "priority": updated.get("priority"),
    }
    required_values = (
        update_payload["id"],
        update_payload["projectId"],
        update_payload["clientId"],
        update_payload["priority"],
    )
    if not all(_clean(value) for value in required_values) or not category_ids:
        return {
            "type": "clarification",
            "tool": "modify_ticket",
            "kind": "ticket_update_draft",
            "args": args,
            "message": (
                "I could not safely preserve the ticket's current project, client, "
                "priority, and category, so no update was made."
            ),
        }

    try:
        response = await aspnet_client.put_form(
            f"api/TicketRegistration/Update/{user_id}",
            _form_data(update_payload),
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Ticket update API rejected request: status=%s body=%s",
            exc.response.status_code,
            exc.response.text[:1000],
        )
        return {
            "type": "error",
            "tool": "modify_ticket",
            "args": args,
            "message": (
                "The ticket update API rejected the request. No changes were "
                "applied; please try again."
            ),
        }
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
        "tool": "modify_ticket",
        "kind": "ticket_updated",
        "args": args,
        "data": {
            "ticketId": _row_value(response_record, ("id", "Id", "ticketId", "TicketId")) or current.get("id"),
            "ticketNo": _row_value(response_record, ("ticketNo", "TicketNo")) or current.get("ticketNo"),
            "changedFields": [field for field, _, _ in changes],
        },
    }
