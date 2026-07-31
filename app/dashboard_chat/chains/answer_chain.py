import json
from typing import Dict, Any, List
from langchain_core.prompts import PromptTemplate
from app.services.llm_service import call_llm_async
from app.dashboard_chat.chains.intent_chain import clean_json_response

ANSWER_TEMPLATE = """You are an AI assistant in a ticketing management system.
You are given a user query, intent, active filters, and the raw data retrieved from the backend API.
Write a concise, helpful summary or explanation of this data for the user, and propose 2-3 relevant follow-up actions/chips.

Rules:
1. Refer ONLY to the provided API Data. Do NOT invent counts, names, or ticket details.
2. If the data is a paginated list of tickets, mention the total count found and what subset is currently shown. E.g., "I found 34 overdue tickets for July 2026. Here are the first 30."
3. Keep the response message concise, brief, and formatted in markdown.
4. For CRM client details, provide only Name, Email, Contact, and Address when those fields are present. Never mention database IDs, created dates, or internal IDs.

Output strictly as a JSON object, with NO other text before or after:
{{
  "message": "formatted markdown summary",
}}

User Query: {query}
Intent: {intent}
Filters: {filters}
API Data: {api_data}

Answer:"""

answer_prompt = PromptTemplate(
    input_variables=["query", "intent", "filters", "api_data"],
    template=ANSWER_TEMPLATE
)

PERFORMANCE_SUMMARY_TEMPLATE = """You summarize an employee's ticket workload from the PERFORMANCE DATA below.
This is a workload report, not a complete evaluation of employee performance.

Rules:
1. Use only the supplied PERFORMANCE DATA. Do not invent counts, dates, causes, trends, or employee facts.
2. Assigned tickets and created tickets are different populations. Discuss them separately and never divide one population by the other.
3. Use the supplied analysis percentages; do not recalculate them or combine unrelated metrics.
4. Mention the requested period only when period.appliedToData is true.
5. Treat missing or null values as unavailable, never as zero.
6. Do not infer productivity, quality, effort, causes, SLA compliance, or trends from ticket volume alone.
7. Do not provide an overall score or rating because no approved scoring rubric is supplied.
8. Do not merely restate ticket counts. Lead with what the percentages mean for resolution progress, remaining backlog, overdue exposure, and follow-up priority; use counts only as supporting evidence.
9. Start with a markdown heading containing the employee name, then return exactly 4 concise markdown bullet points.
10. Include at least two relevant percentages when assignedTickets.total is greater than zero.
11. The final bullet must begin with **Conclusion:** and state the strongest evidence-backed workload conclusion plus the next follow-up priority.
12. Compare assigned and created volume only using analysis.volumeComparison. State that the populations may overlap, so equal volumes do not prove self-assignment.
13. Never include database IDs, employee IDs, internal identifiers, or data not in the payload.

Output strictly as JSON with no other text:
{{
  \"message\": \"markdown insight report\"
}}

PERFORMANCE DATA:
{performance_data}
"""

performance_summary_prompt = PromptTemplate(
    input_variables=["performance_data"],
    template=PERFORMANCE_SUMMARY_TEMPLATE,
)


def _percentage(part: int, total: int) -> float | None:
    return round(part * 100 / total, 1) if total else None


def _performance_analysis(performance_data: Dict[str, Any]) -> Dict[str, Any]:
    def section(name: str) -> Dict[str, Any]:
        tickets = performance_data.get(name) or {}
        total = max(0, int(tickets.get("total") or 0))
        active = max(0, int(tickets.get("active") or 0))
        resolved = max(0, int(tickets.get("completed") or 0)) + max(
            0, int(tickets.get("closed") or 0)
        )
        overdue = max(0, int(tickets.get("overdue") or 0))
        pending_server = max(0, int(tickets.get("pendingOnServer") or 0))
        return {
            "total": total,
            "active": active,
            "resolved": resolved,
            "overdue": overdue,
            "pendingOnServer": pending_server,
            "resolutionRatePct": _percentage(resolved, total),
            "activeRatePct": _percentage(active, total),
            "overdueRatePct": _percentage(overdue, total),
            "pendingServerShareOfActivePct": _percentage(pending_server, active),
        }

    assigned = section("assignedTickets")
    created = section("createdTickets")
    difference = created["total"] - assigned["total"]
    relationship = "matched" if difference == 0 else "higher" if difference > 0 else "lower"
    return {
        "assigned": assigned,
        "created": created,
        "volumeComparison": {
            "createdComparedWithAssigned": relationship,
            "absoluteDifference": abs(difference),
        },
    }


def _performance_summary_payload(performance_data: Dict[str, Any]) -> Dict[str, Any]:
    """Pass only display-safe, summary-relevant fields to the LLM."""
    period = performance_data.get("period") or {}

    def ticket_section(name: str) -> Dict[str, Any]:
        tickets = performance_data.get(name) or {}
        return {
            key: tickets.get(key)
            for key in (
                "total",
                "active",
                "completed",
                "closed",
                "overdue",
                "pendingOnServer",
            )
        }

    return {
        "employeeName": performance_data.get("employeeName"),
        "designation": performance_data.get("designation"),
        "period": {
            "startDate": period.get("startDate"),
            "endDate": period.get("endDate"),
            "appliedToData": period.get("appliedToData") is True,
        },
        "assignedTickets": ticket_section("assignedTickets"),
        "createdTickets": ticket_section("createdTickets"),
        "analysis": _performance_analysis(performance_data),
    }


def _is_analytical_performance_summary(message: str, performance_data: Dict[str, Any]) -> bool:
    bullet_count = sum(
        line.lstrip().startswith(("- ", "• "))
        for line in message.splitlines()
    )
    assigned_total = (_performance_analysis(performance_data).get("assigned") or {}).get("total", 0)
    employee_name = str(performance_data.get("employeeName") or "").strip()
    return (
        message.lstrip().startswith("### ")
        and bullet_count == 4
        and "**Conclusion:**" in message
        and (not assigned_total or message.count("%") >= 2)
        and (not employee_name or employee_name.lower() in message.lower())
    )


async def run_performance_summary_chain(performance_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate an LLM insight report; callers retain a deterministic fallback."""
    prompt = performance_summary_prompt.format(
        performance_data=json.dumps(_performance_summary_payload(performance_data))
    )
    response_text = await call_llm_async(prompt)

    try:
        result = json.loads(clean_json_response(response_text))
        message = result.get("message") if isinstance(result, dict) else None
        if isinstance(message, str) and _is_analytical_performance_summary(message.strip(), performance_data):
            return {"message": message.strip()}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {}

async def run_answer_chain(query: str, intent: str, filters: Dict[str, Any], api_data: Any) -> Dict[str, Any]:
    prompt = answer_prompt.format(
        query=query,
        intent=intent,
        filters=json.dumps(filters),
        api_data=json.dumps(api_data)
    )
    
    response_text = await call_llm_async(prompt)
    clean_text = clean_json_response(response_text)
    
    try:
        return json.loads(clean_text)
    except Exception:
        # Fallback formatting if JSON parsing fails
        # Attempt to make a basic summary of the data
        if intent == "list_overdue_tickets":
            total = api_data.get("totalCount", 0) if isinstance(api_data, dict) else 0
            page_size = api_data.get("pageSize", 30) if isinstance(api_data, dict) else 30
            return {
                "message": f"I found {total} overdue tickets. Here are the first {min(total, page_size)}.",
                # "suggestions": ["Show next 20", "Only high priority", "Group by project"]
            }
        else:
            return {
                "message": "Here is the raw data matching your query.",
                # "suggestions": ["Show overdue tickets", "Summarize this week"]
            }
