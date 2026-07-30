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

PERFORMANCE_SUMMARY_TEMPLATE = """You are an employee performance analyst for a ticketing system.
Write a concise insight report from the PERFORMANCE DATA below.

Rules:
1. Use only the supplied PERFORMANCE DATA. Do not invent counts, dates, causes, trends, or employee facts.
2. Do not repeat dashboard-card labels such as \"Total Assigned\", \"Active Load\", \"Raised Tickets\", \"Pending To Close\", or \"Pending On Server\" as a list. Explain what the numbers mean instead.
3. Return 2-4 concise markdown bullet points. Focus on ratios, workload implications, follow-up priorities, and comparisons that can be calculated from the supplied values.
4. When interpreting raisedTickets, state that it is an assumption (for example, \"assuming raised means initiated by the employee\").
5. If workActivityAvailable is false, do not infer work activity, actions, or active days.
6. Never include database IDs, employee IDs, internal identifiers, or data not in the payload.
7. Provide analytics of the employee performance with some quantitive factors like percentage.
8. Give a overall performance score from 10 to the employee.
9. Use Neural tone to represent the data.




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


def _performance_summary_payload(performance_data: Dict[str, Any]) -> Dict[str, Any]:
    """Pass only display-safe, summary-relevant fields to the LLM."""
    tickets = performance_data.get("tickets") or {}
    return {
        "employeeName": performance_data.get("employeeName"),
        "designation": performance_data.get("designation"),
        "startDate": performance_data.get("startDate"),
        "endDate": performance_data.get("endDate"),
        "workActivityAvailable": bool(performance_data.get("workActivityAvailable")),
        "workActivityCount": performance_data.get("workActivityCount"),
        "activeWorkDays": performance_data.get("activeWorkDays"),
        "tickets": {
            "activeTickets": tickets.get("activeTickets", 0),
            "totalTickets": tickets.get("totalTickets", 0),
            "raisedTickets": tickets.get("raisedTickets", 0),
            "pendingToCloseTickets": tickets.get("pendingToCloseTickets", 0),
            "pendingOnServer": tickets.get("pendingOnServer", 0),
        },
    }


async def run_performance_summary_chain(performance_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate an LLM insight report; callers retain a deterministic fallback."""
    prompt = performance_summary_prompt.format(
        performance_data=json.dumps(_performance_summary_payload(performance_data))
    )
    response_text = await call_llm_async(prompt)

    try:
        result = json.loads(clean_json_response(response_text))
        message = result.get("message") if isinstance(result, dict) else None
        if isinstance(message, str) and message.strip():
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
