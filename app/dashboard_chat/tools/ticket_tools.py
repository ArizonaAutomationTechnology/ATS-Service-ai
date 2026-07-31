import asyncio
import math
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.dashboard_chat.services.aspnet_client import aspnet_client
from app.dashboard_chat.schemas import TicketCard


GLOBAL_DASHBOARD_USER_ID = "00000000-0000-0000-0000-000000000000"

async def fetch_single_page(payload: Dict[str, Any], endpoint: str) -> Dict[str, Any]:
    """Helper to fetch a single page of tickets from the ASP.NET API."""
    try:
        response = await aspnet_client.post(endpoint, payload)
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"Ticket API request failed: {endpoint}") from exc

async def get_tickets(
    entity_id: str,
    block_id: str,
    user_id: str,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    priority: Optional[str] = None,
    project_id: Optional[str] = None,
    category_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    keyword: Optional[str] = None,
    ticket_no: Optional[str] = None,
    page_index: int = 1,
    page_size: int = 30,
    for_report: bool = False,
    employee_scope: bool = False,
) -> Dict[str, Any]:
    """
    Fetches filtered tickets using the existing ASP.NET pagination API.
    Status-based searches use the existing dashboard API so their status
    semantics exactly match the dashboard tabs (for example, In Process also
    includes Pending on Server). For grouped analytics, for_report retrieves
    one complete filtered snapshot from that same API.
    """
    if employee_scope:
        page_size = 12

    base_payload = {
        "entityId": entity_id,
        "blockId": block_id,
        "userId": user_id if employee_scope else assigned_to or (GLOBAL_DASHBOARD_USER_ID if status else user_id),
        "keyword": ticket_no or keyword,
        "client": None,
        "project": project_id,
        "category": category_id,
        "priority": priority,
        "startDate": start_date,
        "endDate": end_date,
        "status": status,
        "statusFilter": None,
        "forReport": for_report,
    }
    if employee_scope:
        endpoint = "api/Dashboard/GetAllTicketsUserWise"
        base_payload["status"] = status or "ALL"
    elif status == "Re Open":
        endpoint = "api/TicketRegistration/GetAllReOpenPaginated"
        base_payload["status"] = None
    elif status:
        endpoint = "api/Dashboard/GetAllTickets"
    else:
        endpoint = "api/TicketRegistration/GetAllPaginated"

    if for_report and employee_scope:
        first_page = await fetch_single_page(
            {**base_payload, "pageIndex": 1, "pageSize": page_size},
            endpoint,
        )
        responses = [first_page]
        total_record = int(first_page.get("totalRecord") or first_page.get("TotalRecord") or 0)
        page_count = math.ceil(total_record / page_size) if total_record else 1
        if page_count > 1:
            responses.extend(await asyncio.gather(*(
                fetch_single_page(
                    {**base_payload, "pageIndex": idx, "pageSize": page_size},
                    endpoint,
                )
                for idx in range(2, page_count + 1)
            )))
        records = []
        for response in responses:
            records.extend(response.get("filteredRecord") or response.get("FilteredRecord") or [])
        records = records[:total_record]
    elif for_report:
        res = await fetch_single_page(
            {**base_payload, "pageIndex": 1, "pageSize": 10},
            endpoint,
        )
        records = res.get("filteredRecord") or res.get("FilteredRecord") or []
        total_record = res.get("totalRecord") or res.get("TotalRecord") or 0

    # Fetch enough physical ASP.NET pages of 10 rows to satisfy the logical page size.
    elif page_size > 10:
        physical_page_size = 10
        pages_per_logical_page = (page_size + physical_page_size - 1) // physical_page_size
        first_physical_page = (page_index - 1) * pages_per_logical_page + 1
        physical_page_indexes = [
            first_physical_page + offset
            for offset in range(pages_per_logical_page)
        ]

        payloads = [
            {**base_payload, "pageIndex": idx, "pageSize": physical_page_size}
            for idx in physical_page_indexes
        ]

        responses = await asyncio.gather(*(fetch_single_page(payload, endpoint) for payload in payloads))

        records = []
        total_record = 0
        for response in responses:
            page_records = response.get("filteredRecord") or response.get("FilteredRecord") or []
            records.extend(page_records)
            total_record = max(total_record, response.get("totalRecord") or response.get("TotalRecord") or 0)
        records = records[:page_size]
    else:
        payload = {**base_payload, "pageIndex": page_index, "pageSize": page_size}
        res = await fetch_single_page(payload, endpoint)
        records = res.get("filteredRecord") or res.get("FilteredRecord") or []
        total_record = res.get("totalRecord") or res.get("TotalRecord") or 0

    # Format the tickets into standard TicketCard objects
    tickets: List[Dict[str, Any]] = []
    now = datetime.now()

    for r in records:
        created_dt_str = r.get("CreatedDate") or r.get("createdDate")
        age_days = 0
        if created_dt_str:
            try:
                # Typical format: 2026-07-02T10:00:00 or 2026-07-02
                clean_dt_str = created_dt_str.split(".")[0].replace("T", " ")
                created_dt = datetime.strptime(clean_dt_str, "%Y-%m-%d %H:%M:%S")
                age_days = (now - created_dt).days
            except Exception:
                try:
                    created_dt = datetime.strptime(created_dt_str[:10], "%Y-%m-%d")
                    age_days = (now - created_dt).days
                except Exception:
                    pass

        due_date_str = r.get("DueDate") or r.get("dueDate")
        if due_date_str and len(due_date_str) > 10:
            due_date_str = due_date_str[:10]

        created_date_str = created_dt_str[:10] if created_dt_str else None

        tickets.append({
            "ticketId": str(r.get("Id") or r.get("id")),
            "ticketNo": str(r.get("TicketNo") or r.get("ticketNo")),
            "title": str(r.get("Title") or r.get("title") or r.get("Description") or r.get("description") or "No Title")[:80],
            "projectName": r.get("ProjectName") or r.get("projectName"),
            "categoryName": r.get("CategoryNameString") or r.get("categoryNameString"),
            "priority": r.get("Priority") or r.get("priority"),
            "status": r.get("Status") or r.get("status"),
            "assignedToName": r.get("AssignToTask") or r.get("assignToTask") or "Unassigned",
            "createdDate": created_date_str,
            "dueDate": due_date_str,
            "ageDays": max(0, age_days)
        })

    return {
        "tickets": tickets,
        "totalCount": total_record,
        "pageIndex": 1 if for_report else page_index,
        "pageSize": len(tickets) if for_report else page_size,
    }

async def get_ticket_history(ticket_id: str) -> List[Dict[str, Any]]:
    """Calls GetTrackerHistory endpoint to retrieve ticket timeline/logs."""
    try:
        response = await aspnet_client.get(f"api/TicketRegistration/GetTrackerHistory/{ticket_id}")
        return response.json()
    except Exception:
        return []


async def get_ticket_record(ticket_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one ticket record so callers can enforce ownership before details."""
    try:
        response = await aspnet_client.get(f"api/TicketRegistration/GetTicketRegData/{ticket_id}")
        data = response.json()
        if not isinstance(data, dict):
            return None
        ticket = data.get("ticketData") or data.get("TicketData")
        return ticket if isinstance(ticket, dict) else None
    except Exception:
        return None


async def get_tickets_created_by_user(
    *,
    user_id: str,
    entity_id: str,
    block_id: str,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Fetch and date-filter tickets created by one user."""
    response = await aspnet_client.get(
        f"api/TicketRegistration/GetAllByUserId/{user_id},{entity_id},{block_id}"
    )
    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("Created-ticket API returned an unexpected response shape")

    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    tickets: List[Dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        created_value = row.get("createdDate") or row.get("CreatedDate")
        if not created_value:
            continue
        try:
            created = datetime.fromisoformat(str(created_value).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if start <= created <= end:
            tickets.append({
                "ticketId": str(row.get("id") or row.get("Id") or ""),
                "ticketNo": str(row.get("ticketNo") or row.get("TicketNo") or ""),
                "status": row.get("status") or row.get("Status"),
                "priority": row.get("priority") or row.get("Priority"),
                "createdDate": created.isoformat(),
            })
    return tickets
