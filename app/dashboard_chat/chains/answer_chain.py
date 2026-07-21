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
4. Provide 2-3 short suggestions (e.g. "Show next 30", "Only high priority", "Group by project", "Show bottleneck users").

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
                "suggestions": ["Show next 20", "Only high priority", "Group by project"]
            }
        else:
            return {
                "message": "Here is the raw data matching your query.",
                "suggestions": ["Show overdue tickets", "Summarize this week"]
            }
