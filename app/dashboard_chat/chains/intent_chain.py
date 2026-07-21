import json
import re
from typing import Dict, Any, Optional
from langchain_core.prompts import PromptTemplate
from app.services.llm_service import call_llm_async

INTENT_TEMPLATE = """You are an AI assistant in a ticketing management system.
Classify the user's message into one of these 5 intents:
- 'list_overdue_tickets': listing tickets that have passed their due date and are not closed/completed/rejected.
- 'weekly_summary': summarizing general ticket counts, status, and activity for the week.
- 'bottleneck_users': identifying users who have high workload or too many open/overdue tickets.
- 'project_health': evaluating active project status, counts, or bottleneck projects.
- 'sla_risk': identifying tickets due to breach SLA (e.g. within next 24/48 hours or this week).

If the message is a follow-up or modification of the previous query, keep the previous intent and update/add the filters.
Examples of follow-ups:
- "Show next 20": increment page index (set next page indicator).
- "Only high priority": add priority="High" to filters.
- "Only for LIMS project": add projectName="LIMS" to filters.

Previous State:
- Last Intent: {last_intent}
- Last Filters: {last_filters}

Extract these filter entities:
- 'dateRangeText': natural language date phrase (e.g., "today", "this week", "this month", "next 48 hours", "July 2026").
- 'priority': "High", "Medium", or "Low".
- 'projectName': Name of a project (e.g., "LIMS").
- 'categoryName': Name of a category.
- 'userName': Name of an employee/user.

Requirements by intent:
- 'list_overdue_tickets' requires 'dateRangeText' filter. If missing, requiresClarification must be true and add 'dateRange' to missingFilters.
- 'weekly_summary' defaults 'dateRangeText' to "this week" if not specified.
- 'bottleneck_users' requires 'dateRangeText' filter. If missing, requiresClarification must be true and add 'dateRange' to missingFilters.
- 'project_health' requires either 'projectName' or 'dateRangeText'. If both missing, requiresClarification must be true and add 'projectOrDateRange' to missingFilters.
- 'sla_risk' requires 'dateRangeText' filter (e.g., "next 24 hours", "next 48 hours", "this week"). If missing, requiresClarification must be true and add 'dateRange' to missingFilters.

If you don't know the intent or if it's completely unrelated, set intent to null.

Output strictly as a JSON object, with NO other text before or after:
{{
  "intent": "list_overdue_tickets" or "weekly_summary" or "bottleneck_users" or "project_health" or "sla_risk" or null,
  "confidence": 0.0 to 1.0,
  "filters": {{
    "dateRangeText": string or null,
    "priority": "High" or "Medium" or "Low" or null,
    "projectName": string or null,
    "categoryName": string or null,
    "userName": string or null
  }},
  "requiresClarification": true or false,
  "missingFilters": ["dateRange"] or ["projectOrDateRange"] or [],
  "isNextPageRequest": true or false
}}

User Message: {message}

Answer:"""

intent_prompt = PromptTemplate(
    input_variables=["message", "last_intent", "last_filters"],
    template=INTENT_TEMPLATE
)

def clean_json_response(text: str) -> str:
    # Remove markdown code block markers if present
    text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()

async def run_intent_chain(message: str, previous_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not previous_state:
        previous_state = {
            "lastIntent": None,
            "filters": {}
        }

    last_intent = previous_state.get("lastIntent")
    last_filters = json.dumps(previous_state.get("filters", {}))

    prompt = intent_prompt.format(
        message=message,
        last_intent=last_intent,
        last_filters=last_filters
    )

    response_text = await call_llm_async(prompt)
    clean_text = clean_json_response(response_text)
    
    try:
        data = json.loads(clean_text)
        return data
    except Exception:
        # Fallback if LLM parsing fails
        return {
            "intent": None,
            "confidence": 0.0,
            "filters": {
                "dateRangeText": None,
                "priority": None,
                "projectName": None,
                "categoryName": None,
                "userName": None
            },
            "requiresClarification": False,
            "missingFilters": [],
            "isNextPageRequest": False
        }
