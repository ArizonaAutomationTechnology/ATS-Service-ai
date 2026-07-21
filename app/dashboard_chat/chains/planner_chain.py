import json
import re
from typing import Any, Dict, Optional
from langchain_core.prompts import PromptTemplate

from app.dashboard_chat.chains.intent_chain import clean_json_response
from app.dashboard_chat.tool_registry import get_tool_catalog_for_prompt
from app.services.llm_service import call_llm_async


PLANNER_TEMPLATE = """You are a planning assistant for a ticketing management system.
Your job is to convert the user's natural-language request into a safe JSON tool plan.

You are NOT limited to fixed intents. Pick one or more tools from the catalog.
Do not invent tools. Do not answer from memory. If required information is missing, ask one clarification question.

Available tools:
{tool_catalog}

Previous conversation state:
{conversation_state}

Planning rules:
1. Output strictly valid JSON only. No markdown and no extra text.
2. Use follow-up context. For "show next 20", reuse the previous tool and args and set isNextPageRequest=true.
3. For broad ticket list requests, include a dateRangeText unless the user provides ticketNo, projectName, userName, categoryName, or another narrow filter.
4. Use pageSize at most 30.
5. Prefer search_tickets for questions about ticket lists, status, priority, project, category, user, ticket number, or keyword.
6. Prefer get_ticket_history only when the user references one ticket number or ticket id.
7. Prefer get_dashboard_counts, get_project_ticket_counts, get_priority_counts, or get_user_workloads for aggregate questions.
8. Use group_tickets only after search_tickets when the user asks to group, compare, break down, or summarize by project, category, user, priority, assignee, employee, or status.
9. If the user asks "which project/user/category/priority/status has overdue/reopened/closed/etc.", do NOT use get_dashboard_counts. Use search_tickets with the mentioned status, then group_tickets by the requested dimension.
10. A ticket status such as Overdue, ReOpen, In Process, Closed, Completed, Raised, or Rejected is a valid narrowing filter for grouped/count questions, even without a date range.
11. If the user asks how many tickets were raised/created in a month, use get_dashboard_counts with metric="monthly_volume" and the dateRangeText so the count matches Monthly Volume Intake. Only use status="Raised" when they ask for tickets currently/now in the Raised state.
12. If the user asks for a vague "all tickets" query with no date range, status, or narrow filter, ask for a date range.
13. If the request is unrelated to tickets, projects, users, workload, or dashboard analytics, ask how you can help with ticket data.

Output schema:
{{
  "needs_clarification": true or false,
  "clarification_question": string or null,
  "suggestions": [string],
  "tool_calls": [
    {{
      "tool": "tool_name_from_catalog",
      "args": {{ }}
    }}
  ],
  "response_format": "ticket_list | grouped_ticket_summary | summary | project_health | user_workload | ticket_history | counts | clarification | error",
  "isNextPageRequest": true or false
}}

User message:
{message}

JSON answer:"""

planner_prompt = PromptTemplate(
    input_variables=["message", "conversation_state", "tool_catalog"],
    template=PLANNER_TEMPLATE,
)


def _detect_status(lower: str) -> Optional[str]:
    status_patterns = [
        ("Overdue", ["overdue", "over due"]),
        ("Re Open", ["reopen", "re-open", "reopened", "re opened", "re open"]),
        ("In Process", ["in process", "inprocess", "processing"]),
        ("Completed", ["completed", "complete"]),
        ("Closed", ["closed", "close ticket"]),
        ("Rejected", ["rejected", "reject"]),
        ("Raised", ["raised", "open tickets", "new tickets"]),
    ]
    for status, phrases in status_patterns:
        if any(phrase in lower for phrase in phrases):
            return status
    return None


def _detect_group_by(lower: str) -> Optional[str]:
    dimension_patterns = [
        ("projectName", ["project", "projects"]),
        ("assignedToName", ["assignee", "assigned", "user", "users", "employee", "employees", "person", "people", "developer", "developers"]),
        ("categoryName", ["category", "categories"]),
        ("priority", ["priority", "priorities"]),
        ("status", ["status", "statuses"]),
    ]
    analytical_words = [
        "which",
        "who",
        "whose",
        "most",
        "highest",
        "maximum",
        "max",
        "breakdown",
        "break down",
        "group",
        "grouped",
        "by ",
        "wise",
        "distribution",
        "split",
    ]
    if not any(word in lower for word in analytical_words):
        return None
    if re.search(r"\b(who|whose)\b", lower):
        return "assignedToName"
    for group_by, phrases in dimension_patterns:
        if any(re.search(rf"\b{re.escape(phrase)}\b", lower) for phrase in phrases):
            return group_by
    return None


def _detect_date_range_text(lower: str) -> Optional[str]:
    date_phrases = [
        "today",
        "yesterday",
        "this week",
        "last week",
        "this month",
        "last month",
        "this year",
        "last year",
    ]
    for phrase in date_phrases:
        if phrase in lower:
            return phrase
    last_days = re.search(r"\blast\s+\d+\s+days?\b", lower)
    if last_days:
        return last_days.group(0)
    months = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
    month_match = re.search(rf"\b({months})(?:\s+20\d{{2}})?\b", lower)
    if month_match:
        return month_match.group(0)
    range_match = re.search(r"\bfrom\s+.+\s+to\s+.+", lower)
    if range_match:
        return range_match.group(0)
    return None


def _is_count_question(lower: str) -> bool:
    return any(phrase in lower for phrase in ["how many", "count", "total", "number of"])


def _is_current_context(lower: str) -> bool:
    return any(phrase in lower for phrase in ["current", "currently", "now", "right now", "at present", "as of now"])


def _extract_person_name_for_status_query(message: str, status: str) -> Optional[str]:
    lower = message.lower()
    phrases_by_status = {
        "Re Open": ["reopen", "re-open", "re opened", "reopened", "re open"],
        "Overdue": ["overdue", "over due"],
        "In Process": ["in process", "inprocess", "processing"],
        "Completed": ["completed", "complete"],
        "Closed": ["closed"],
        "Rejected": ["rejected", "reject"],
        "Raised": ["raised"],
    }
    for phrase in phrases_by_status.get(status, []):
        match = re.search(rf"(.+?)\b{re.escape(phrase)}\b", lower)
        if not match:
            continue
        candidate = re.sub(r"\b(show|list|get|find|all|the|tickets?|assigned|to|of|for)\b", " ", match.group(1))
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate:
            return candidate
    return None


def _rule_based_person_status_plan(message: str) -> Optional[Dict[str, Any]]:
    lower = message.lower()
    status = _detect_status(lower)
    if not status or "ticket" not in lower:
        return None

    user_name = _extract_person_name_for_status_query(message, status)
    if not user_name:
        return None

    args: Dict[str, Any] = {
        "status": status,
        "userName": user_name,
        "pageIndex": 1,
        "pageSize": 30,
    }
    date_range_text = _detect_date_range_text(lower)
    if date_range_text:
        args["dateRangeText"] = date_range_text

    return {
        "needs_clarification": False,
        "clarification_question": None,
        "suggestions": [],
        "tool_calls": [
            {
                "tool": "search_tickets",
                "args": args,
            }
        ],
        "response_format": "ticket_list",
        "isNextPageRequest": False,
    }


def _rule_based_raised_count_plan(message: str) -> Optional[Dict[str, Any]]:
    lower = message.lower()
    if "raised" not in lower or not _is_count_question(lower):
        return None

    date_range_text = _detect_date_range_text(lower)
    if _is_current_context(lower) or not date_range_text:
        return {
            "needs_clarification": False,
            "clarification_question": None,
            "suggestions": [],
            "tool_calls": [
                {
                    "tool": "search_tickets",
                    "args": {
                        "status": "Raised",
                        "pageIndex": 1,
                        "pageSize": 30,
                    },
                }
            ],
            "response_format": "ticket_list",
            "isNextPageRequest": False,
        }

    return {
        "needs_clarification": False,
        "clarification_question": None,
        "suggestions": [],
        "tool_calls": [
            {
                "tool": "get_dashboard_counts",
                "args": {
                    "metric": "monthly_volume",
                    "dateRangeText": date_range_text,
                },
            }
        ],
        "response_format": "monthly_ticket_count",
        "isNextPageRequest": False,
    }


def _rule_based_analytical_plan(message: str) -> Optional[Dict[str, Any]]:
    lower = message.lower()
    status = _detect_status(lower)
    group_by = _detect_group_by(lower)
    if not (status and group_by):
        return None

    args: Dict[str, Any] = {
        "status": status,
        "pageIndex": 1,
        "pageSize": 30,
    }
    date_range_text = _detect_date_range_text(lower)
    if date_range_text:
        args["dateRangeText"] = date_range_text

    return {
        "needs_clarification": False,
        "clarification_question": None,
        "suggestions": [],
        "tool_calls": [
            {
                "tool": "search_tickets",
                "args": args,
            },
            {
                "tool": "group_tickets",
                "args": {
                    "groupBy": group_by,
                },
            },
        ],
        "response_format": "grouped_ticket_summary",
        "isNextPageRequest": False,
    }


def _fallback_plan(message: str, previous_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    lower = message.lower()
    if "next" in lower and previous_state and previous_state.get("lastTool"):
        return {
            "needs_clarification": False,
            "clarification_question": None,
            "suggestions": [],
            "tool_calls": [
                {
                    "tool": previous_state.get("lastTool"),
                    "args": previous_state.get("lastArgs") or {},
                }
            ],
            "response_format": previous_state.get("lastResponseFormat") or "ticket_list",
            "isNextPageRequest": True,
        }

    if any(word in lower for word in ["overdue", "reopen", "re-open", "closed", "completed", "in process", "raised", "rejected", "ticket"]):
        status = None
        if "overdue" in lower:
            status = "Overdue"
        elif "reopen" in lower or "re-open" in lower or "re open" in lower:
            status = "Re Open"
        elif "closed" in lower:
            status = "Closed"
        elif "completed" in lower:
            status = "Completed"
        elif "in process" in lower:
            status = "In Process"
        elif "raised" in lower:
            status = "Raised"
        elif "rejected" in lower:
            status = "Rejected"

        has_date_hint = any(phrase in lower for phrase in ["today", "yesterday", "week", "month", "year", "last ", "this ", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])
        if not has_date_hint and not any(word in lower for word in ["project", "user", "category", "priority", "ticket no", "ticket number"]):
            return {
                "needs_clarification": True,
                "clarification_question": "Which date range should I use for this ticket search?",
                "suggestions": ["This month", "Last month", "This week", "Today"],
                "tool_calls": [],
                "response_format": "clarification",
                "isNextPageRequest": False,
            }

        return {
            "needs_clarification": False,
            "clarification_question": None,
            "suggestions": [],
            "tool_calls": [
                {
                    "tool": "search_tickets",
                    "args": {
                        "status": status,
                        "dateRangeText": "this month" if "this month" in lower else ("this week" if "this week" in lower else None),
                        "pageIndex": 1,
                        "pageSize": 30,
                    },
                }
            ],
            "response_format": "ticket_list",
            "isNextPageRequest": False,
        }

    return {
        "needs_clarification": True,
        "clarification_question": "I can help with tickets, projects, users, workload, and dashboard analytics. What would you like to check?",
        "suggestions": ["Show overdue tickets", "Project health", "User workload", "Ticket counts"],
        "tool_calls": [],
        "response_format": "clarification",
        "isNextPageRequest": False,
    }


async def run_planner_chain(message: str, previous_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if previous_state is None:
        previous_state = {}

    raised_count_plan = _rule_based_raised_count_plan(message)
    if raised_count_plan:
        return raised_count_plan

    analytical_plan = _rule_based_analytical_plan(message)
    if analytical_plan:
        return analytical_plan

    person_status_plan = _rule_based_person_status_plan(message)
    if person_status_plan:
        return person_status_plan

    prompt = planner_prompt.format(
        message=message,
        conversation_state=json.dumps(previous_state, default=str),
        tool_catalog=get_tool_catalog_for_prompt(),
    )

    response_text = await call_llm_async(prompt)
    clean_text = clean_json_response(response_text)

    try:
        data = json.loads(clean_text)
        if not isinstance(data.get("tool_calls", []), list):
            data["tool_calls"] = []
        data.setdefault("needs_clarification", False)
        data.setdefault("clarification_question", None)
        data.setdefault("suggestions", [])
        data.setdefault("response_format", "summary")
        data.setdefault("isNextPageRequest", False)
        return data
    except Exception:
        return _fallback_plan(message, previous_state)
