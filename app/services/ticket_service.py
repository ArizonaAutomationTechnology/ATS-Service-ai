import asyncio
import math
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional

from app.dashboard_chat.services.aspnet_client import aspnet_client


_ACTIVE_SIMILARITY_STATUSES = {
    "raised",
    "in process",
    "pending on server",
    "overdue",
    "overdue pending on server",
    "re open",
    "reopen pending on server",
}
_WORKLOAD_STATUSES = {"in process", "pending on server"}
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


class TicketApiError(RuntimeError):
    pass


def _value(row: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


async def _get_ticket_rows(
    entity_id: str,
    block_id: str,
    *,
    keyword: Optional[str] = None,
    client_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    payload = {
        "entityId": entity_id,
        "blockId": block_id,
        "userId": _EMPTY_GUID,
        "pageIndex": 1,
        "pageSize": 10,
        "keyword": keyword,
        "client": client_id,
        "project": project_id,
        "category": None,
        "priority": None,
        "startDate": None,
        "endDate": None,
        "status": status,
        "statusFilter": None,
        "forReport": True,
    }

    async def fetch(page_index: int) -> Dict[str, Any]:
        try:
            response = await aspnet_client.post(
                "api/TicketRegistration/GetAllPaginated",
                {**payload, "pageIndex": page_index},
            )
            data = response.json()
        except Exception as exc:
            raise TicketApiError("ASP.NET ticket API request failed.") from exc
        if not isinstance(data, dict):
            raise TicketApiError("ASP.NET ticket API returned an unexpected response.")
        return data

    first = await fetch(1)
    rows = first.get("filteredRecord") or first.get("FilteredRecord") or []
    if not isinstance(rows, list):
        raise TicketApiError("ASP.NET ticket API returned an unexpected response.")

    try:
        total = int(first.get("totalRecord") or first.get("TotalRecord") or len(rows))
    except (TypeError, ValueError) as exc:
        raise TicketApiError("ASP.NET ticket API returned an invalid record count.") from exc

    if len(rows) < total:
        for page_index in range(2, math.ceil(total / 10) + 1):
            page = await fetch(page_index)
            page_rows = page.get("filteredRecord") or page.get("FilteredRecord") or []
            if not isinstance(page_rows, list):
                raise TicketApiError("ASP.NET ticket API returned an unexpected response.")
            rows.extend(page_rows)

    unique: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            unique[str(_value(row, "id", "Id") or index)] = row
    return list(unique.values())


async def get_ticket_details(
    ticket_id: str,
    entity_id: str,
    block_id: str,
) -> Optional[Dict[str, Any]]:
    try:
        resolved_id = str(uuid.UUID(ticket_id))
    except (TypeError, ValueError, AttributeError):
        rows = await _get_ticket_rows(entity_id, block_id, keyword=ticket_id)
        match = next(
            (
                row
                for row in rows
                if str(_value(row, "ticketNo", "TicketNo") or "").casefold()
                == ticket_id.casefold()
            ),
            None,
        )
        if not match:
            return None
        resolved_id = str(_value(match, "id", "Id") or "")
        if not resolved_id:
            return None

    try:
        response = await aspnet_client.get(
            f"api/TicketRegistration/GetTicketRegData/{resolved_id}"
        )
        data = response.json()
    except Exception as exc:
        raise TicketApiError("ASP.NET ticket-detail API request failed.") from exc

    if not isinstance(data, dict):
        raise TicketApiError("ASP.NET ticket-detail API returned an unexpected response.")
    ticket = data.get("ticketData") or data.get("TicketData")
    if not isinstance(ticket, dict):
        return None

    return {
        "id": _value(ticket, "id", "Id"),
        "ticketNo": _value(ticket, "ticketNo", "TicketNo"),
        "title": _value(ticket, "title", "Title"),
        "description": _value(ticket, "description", "Description"),
        "priority": _value(ticket, "priority", "Priority"),
        "status": _value(ticket, "status", "Status"),
        "assignTo": _value(ticket, "assignTo", "AssignTo"),
        "AssigneeName": _value(ticket, "assignToTask", "AssignToTask"),
        "AssigneeEmpId": _value(ticket, "empId", "EmpId"),
        "ProjectName": _value(ticket, "projectName", "ProjectName"),
        "category": _value(ticket, "categoryNameString", "CategoryNameString"),
    }


async def get_all_employees(entity_id: str, block_id: str) -> List[Dict[str, Any]]:
    try:
        response = await aspnet_client.get(
            f"api/UserManagement/GetAllActive/{entity_id},{block_id}"
        )
        rows = response.json()
    except Exception as exc:
        raise TicketApiError("ASP.NET employee API request failed.") from exc
    if not isinstance(rows, list):
        raise TicketApiError("ASP.NET employee API returned an unexpected response.")

    employees = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        emp_id = str(_value(row, "empId", "EmpId") or "")
        name = str(_value(row, "name", "Name") or "")
        suffix = f"({emp_id})"
        if emp_id and name.endswith(suffix):
            name = name[: -len(suffix)]
        employees.append(
            {
                "Name": name,
                "EmpId": emp_id,
                "Designation": _value(row, "designationName", "DesignationName"),
            }
        )
    return employees


async def get_employee_project_workloads(
    entity_id: str,
    block_id: str,
) -> List[Dict[str, Any]]:
    rows_by_status = await asyncio.gather(
        *(
            _get_ticket_rows(entity_id, block_id, status=status)
            for status in ("In Process", "Pending on Server")
        )
    )
    rows = [row for status_rows in rows_by_status for row in status_rows]
    counts = Counter()
    for row in rows:
        if str(_value(row, "status", "Status") or "").casefold() not in _WORKLOAD_STATUSES:
            continue
        employee_name = _value(row, "assignToTask", "AssignToTask")
        employee_id = _value(row, "empId", "EmpId")
        project_name = _value(row, "projectName", "ProjectName")
        if employee_name and employee_id and project_name:
            counts[(str(employee_name), str(employee_id), str(project_name))] += 1

    return [
        {
            "EmployeeName": employee_name,
            "EmployeeId": employee_id,
            "ProjectName": project_name,
            "TicketCount": count,
        }
        for (employee_name, employee_id, project_name), count in sorted(counts.items())
    ]


async def get_active_tickets_candidates(
    entity_id: str,
    block_id: str,
    client_id: str,
    project_id: str,
) -> List[Dict[str, Any]]:
    rows = await _get_ticket_rows(
        entity_id,
        block_id,
        client_id=client_id,
        project_id=project_id,
    )
    rows = [
        row
        for row in rows
        if str(_value(row, "status", "Status") or "").casefold()
        in _ACTIVE_SIMILARITY_STATUSES
    ]
    rows.sort(
        key=lambda row: str(_value(row, "createdDate", "CreatedDate") or ""),
        reverse=True,
    )
    return [
        {
            "ticketId": str(_value(row, "id", "Id") or ""),
            "ticketNo": str(_value(row, "ticketNo", "TicketNo") or ""),
            "status": str(_value(row, "status", "Status") or ""),
            "priority": str(_value(row, "priority", "Priority") or ""),
            "description": str(_value(row, "description", "Description") or ""),
        }
        for row in rows[:50]
    ]
