import re
import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.schemas import (
    PromptRequest, 
    AssistantResponse, 
    DatabasePromptRequest, 
    AdminPromptRequest, 
    AdminAssistantResponse,
    SimilarTicketsRequest,
    SimilarTicketsResponse,
    SimilarTicketMatch
)
from app.services import ticket_service, prompt_service, llm_service, nl2sql_service


router = APIRouter(prefix="/api/assistant", tags=["assistant"])

@router.post("/ask", response_model=AssistantResponse)
async def ask_assistant(request: PromptRequest, db: Session = Depends(get_db)):
    # 1. Scan user prompt for employee or assignment matching keywords
    # If the user is just summarizing the ticket description, we do NOT load the employee directory.
    # This prevents sending 41 employee records to the context window, dramatically speeding up CPU inference.
    prompt_lower = request.prompt.lower()
    needs_employees = any(
        kw in prompt_lower 
        for kw in ["assign", "recommend", "suggest", "suitable", "who should", "who to", "allocation", "employee", "people"]
    )

    # 2. Retrieve ticket details (synchronous call, FastAPI executes this in a threadpool)
    ticket = ticket_service.get_ticket_details(db, request.ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket with ID {request.ticket_id} not found."
        )
        
    # 3. Conditionally retrieve active employees directory & workloads
    employees = []
    workloads = []
    if needs_employees:
        employees = ticket_service.get_all_employees(db)
        workloads = ticket_service.get_employee_project_workloads(db)
    
    # 4. Assemble the prompt context
    full_prompt = prompt_service.assemble_prompt(ticket, employees, request.prompt, workloads)
    
    # 5. Invoke LLM asynchronously
    ai_response = await llm_service.call_llm_async(full_prompt)
    
    # 6. Return JSON response
    return AssistantResponse(text=ai_response, status="success")

@router.post("/similar-tickets", response_model=SimilarTicketsResponse)
async def check_similar_tickets(request: SimilarTicketsRequest, db: Session = Depends(get_db)):
    # 1. Fetch candidates from database
    candidates = ticket_service.get_active_tickets_candidates(
        db,
        entity_id=request.entityId,
        block_id=request.blockId,
        client_id=request.clientId,
        project_id=request.projectId
    )
    
    # If no active candidates in database, return immediately
    if not candidates:
        return SimilarTicketsResponse(hasSimilarTickets=False, matches=[])
        
    # Map candidates by ticketId for validation (and safety/hallucination checks)
    candidate_map = {c["ticketId"]: c for c in candidates}
    
    # 2. Assemble prompt
    prompt = prompt_service.format_duplicate_check_prompt(request.description, candidates)
    
    # 3. Call LLM
    raw_response = await llm_service.call_llm_async(prompt)
    
    # Helper to parse and filter JSON response
    def try_parse_matches(llm_output: str):
        cleaned = llm_output.strip()
        # strip markdown code blocks if any
        import re
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
            
        data = json.loads(cleaned)
        if not isinstance(data, dict) or "matches" not in data:
            raise ValueError("Invalid format: expected object with 'matches' list")
            
        matches_list = data["matches"]
        validated_matches = []
        for m in matches_list:
            t_id = m.get("ticketId")
            # Safety checks:
            # - Must be a valid candidate ticket ID from our query (prevents hallucination)
            # - similar must be True, OR similarity >= 0.75
            similarity = m.get("similarity", 0.0)
            similar_flag = m.get("similar", False)
            
            if t_id in candidate_map and (similar_flag is True or similarity >= 0.75):
                candidate = candidate_map[t_id]
                # Build match using database values where possible to guarantee accuracy, 
                # but similarity and reason come from LLM
                validated_matches.append(
                    SimilarTicketMatch(
                        ticketId=candidate["ticketId"],
                        ticketNo=candidate["ticketNo"],
                        status=candidate["status"],
                        priority=candidate["priority"],
                        description=candidate["description"],
                        similarity=float(similarity),
                        reason=m.get("reason", "Description meaning is similar.")
                    )
                )
        return validated_matches

    # 4. JSON parsing & single retry safety step
    matches = []
    import logging
    logger = logging.getLogger("uvicorn.error")
    try:
        matches = try_parse_matches(raw_response)
    except Exception as e:
        logger.warning(f"Failed to parse LLM JSON response. Error: {str(e)}. Retrying once...")
        # Retry prompt
        retry_prompt = prompt + "\n\nCRITICAL: Return ONLY valid JSON matching the specified format. Do not wrap in markdown or include conversational text."
        try:
            retry_response = await llm_service.call_llm_async(retry_prompt)
            matches = try_parse_matches(retry_response)
        except Exception as retry_err:
            logger.error(f"Retry parsing also failed. Error: {str(retry_err)}. Returning empty matches.")
            matches = []

    # Sort matches by similarity descending
    matches.sort(key=lambda x: x.similarity, reverse=True)
    
    return SimilarTicketsResponse(
        hasSimilarTickets=len(matches) > 0,
        matches=matches
    )
@router.post("/admin/ask", response_model=AdminAssistantResponse)
async def ask_admin_assistant(request: AdminPromptRequest, db: Session = Depends(get_db)):

    # 1. Retrieve all workloads from database
    workloads = ticket_service.get_employee_project_workloads(db)
    
    # 2. Assemble the prompt context
    full_prompt = prompt_service.format_admin_prompt(workloads, request.Prompt)
    
    # 3. Call LLM
    ai_response = await llm_service.call_llm_async(full_prompt)
    
    # 4. Handle success status
    success = not ai_response.startswith("Error")
    
    # 5. Return response matching exact schemas keys
    return AdminAssistantResponse(Success=success, Response=ai_response)

# @router.post("/query-db", response_model=AssistantResponse)
# async def query_database(request: DatabasePromptRequest, db: Session = Depends(get_db)):
#     """
#     Translates the natural language query into SQL, executes it against the database, 
#     and returns a summary explanation generated by the LLM.
#     """
#     response_text = await nl2sql_service.execute_natural_query(db, request.prompt)
#     return AssistantResponse(text=response_text, status="success")


