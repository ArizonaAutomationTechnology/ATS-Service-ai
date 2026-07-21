import json
from typing import Dict, Any
from langchain_core.prompts import PromptTemplate
from app.services.llm_service import call_llm_async
from app.dashboard_chat.chains.intent_chain import clean_json_response

CLARIFICATION_TEMPLATE = """You are an AI assistant in a ticketing management system.
The user query is missing a required filter '{missing_filter}' for the intent '{intent}'.
Generate a single, polite, and very short clarification question asking for this missing filter.
Include 3-4 typical examples/choices in the question itself.

Output strictly as a JSON object, with NO other text before or after:
{{
  "message": "Clarification question text",
  "suggestions": ["suggestion 1", "suggestion 2", "suggestion 3", "suggestion 4"]
}}

Examples of date range suggestions: ["This month", "Last month", "This week", "Today"]

Answer:"""

clarification_prompt = PromptTemplate(
    input_variables=["intent", "missing_filter"],
    template=CLARIFICATION_TEMPLATE
)

async def run_clarification_chain(intent: str, missing_filter: str) -> Dict[str, Any]:
    prompt = clarification_prompt.format(intent=intent, missing_filter=missing_filter)
    response_text = await call_llm_async(prompt)
    clean_text = clean_json_response(response_text)
    
    try:
        return json.loads(clean_text)
    except Exception:
        # Fallback responses
        if missing_filter == "dateRange":
            return {
                "message": "For which period should I check overdue tickets: this month, last month, this week, or a custom range?",
                "suggestions": ["This month", "Last month", "This week", "Today"]
            }
        elif missing_filter == "projectOrDateRange":
            return {
                "message": "Please specify a project name or a date range (e.g. LIMS, this month) to check project health.",
                "suggestions": ["LIMS Project", "This month", "This week"]
            }
        else:
            return {
                "message": f"Could you please specify the {missing_filter}?",
                "suggestions": []
            }
