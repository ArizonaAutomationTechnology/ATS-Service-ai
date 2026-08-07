import asyncio
import re
import json
import logging
from difflib import SequenceMatcher
from pathlib import Path
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool
from app.config import settings
from app.models.schemas import (
    PromptRequest, 
    AssistantResponse, 
    AdminPromptRequest, 
    AdminAssistantResponse,
    SimilarTicketsRequest,
    SimilarTicketsResponse,
    SimilarTicketMatch,
    AudioDescriptionResponse,
)
from app.services import ticket_service, prompt_service, llm_service, speech_service


router = APIRouter(prefix="/api/assistant", tags=["assistant"])
logger = logging.getLogger("uvicorn.error")

_AUDIO_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-wav": ".wav",
}
_DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
    "additionalProperties": False,
}
_ASSIGNMENT_WORDS = (
    "assign",
    "assignment",
    "allocate",
    "allocation",
    "delegate",
    "delegation",
    "recommend",
    "recommendation",
    "suggest",
    "suggestion",
    "suitable",
    "candidate",
    "employee",
    "people",
)


def needs_employee_context(prompt: str) -> bool:
    """Return whether a ticket question needs the employee directory."""
    prompt_lower = prompt.lower()
    words = re.findall(r"[a-z]+", prompt_lower)
    return (
        any(word in _ASSIGNMENT_WORDS for word in words)
        or "who should" in prompt_lower
        or "who to" in prompt_lower
        or (
            any(word in {"who", "whom"} for word in words)
            and any(word in {"ticket", "task", "issue"} for word in words)
        )
        or any(
            # Accept one-character typos such as "asign" without broadening the
            # request to unrelated ticket-summary prompts.
            len(word) >= 5 and any(
                SequenceMatcher(None, word, assignment_word).ratio() >= 0.8
                for assignment_word in _ASSIGNMENT_WORDS
            )
            for word in words
        )
    )


async def save_temporary_audio(audio: UploadFile) -> Path:
    content_type = (audio.content_type or "").lower()
    suffix = _AUDIO_SUFFIXES.get(content_type)
    if not suffix:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload an MP3, M4A, OGG, WAV, or WebM audio file.",
        )

    temporary_path: Path | None = None
    total_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary_file:
            temporary_path = Path(temporary_file.name)
            while chunk := await audio.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.MAX_AUDIO_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="The audio recording exceeds the allowed file size.",
                    )
                temporary_file.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The audio recording is empty.")
        return temporary_path
    except Exception:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise
    finally:
        await audio.close()


@router.post("/ticket-description/from-audio", response_model=AudioDescriptionResponse)
async def create_ticket_description_from_audio(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
    project_context: str | None = Form(None),
):
    """Translate uploaded speech and return a user-reviewable ticket description draft."""
    temporary_path: Path | None = None
    try:
        temporary_path = await save_temporary_audio(audio)
        translation = await run_in_threadpool(speech_service.translate_audio, temporary_path, language)
    except speech_service.SpeechServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

    warnings: list[str] = []
    polished_description: str | None = None
    try:
        prompt = prompt_service.format_ticket_description_polish_prompt(translation.text, project_context)
        result = await llm_service.call_llm_json_async(prompt, _DESCRIPTION_SCHEMA)
        description = result.get("description")
        if not isinstance(description, str) or not description.strip():
            raise llm_service.LlmServiceError("Ollama returned an empty ticket description.")
        polished_description = description.strip()
    except llm_service.LlmServiceError as exc:
        logger.warning("Ticket-description polishing unavailable: %s", exc)
        warnings.append("Translation succeeded, but description polishing is temporarily unavailable.")

    return AudioDescriptionResponse(
        status="success" if polished_description else "partial_success",
        detectedLanguage=translation.detected_language,
        languageConfidence=translation.language_confidence,
        translatedText=translation.text,
        polishedDescription=polished_description,
        warnings=warnings,
    )

@router.post("/ask", response_model=AssistantResponse)
async def ask_assistant(request: PromptRequest):
    # 1. Scan user prompt for employee or assignment intent.
    # If the user is just summarizing the ticket description, we do NOT load the employee directory.
    # This prevents sending 41 employee records to the context window, dramatically speeding up CPU inference.
    needs_employees = needs_employee_context(request.prompt)

    # ATS data is intentionally fetched through the deployed ASP.NET API, not SQL.
    try:
        ticket = await ticket_service.get_ticket_details(
            request.ticket_id,
            request.entityId,
            request.blockId,
        )
    except ticket_service.TicketApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The ATS API is temporarily unavailable.",
        ) from exc
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket with ID {request.ticket_id} not found."
        )
        
    # 3. Conditionally retrieve active employees directory & workloads
    employees = []
    workloads = []
    if needs_employees:
        try:
            employees, workloads = await asyncio.gather(
                ticket_service.get_all_employees(request.entityId, request.blockId),
                ticket_service.get_employee_project_workloads(request.entityId, request.blockId),
            )
        except ticket_service.TicketApiError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The ATS API is temporarily unavailable.",
            ) from exc
    
    # 4. Assemble the prompt context
    full_prompt = prompt_service.assemble_prompt(ticket, employees, request.prompt, workloads)
    
    # 5. Invoke LLM asynchronously
    ai_response = await llm_service.call_llm_async(full_prompt)
    
    # 6. Return JSON response
    return AssistantResponse(text=ai_response, status="success")

@router.post("/similar-tickets", response_model=SimilarTicketsResponse)
async def check_similar_tickets(request: SimilarTicketsRequest):
    try:
        candidates = await ticket_service.get_active_tickets_candidates(
            entity_id=request.entityId,
            block_id=request.blockId,
            client_id=request.clientId,
            project_id=request.projectId,
        )
    except ticket_service.TicketApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The ATS API is temporarily unavailable.",
        ) from exc
    
    # If the ATS API has no active candidates, return immediately.
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
                # Build matches from authoritative ASP.NET API values.
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
async def ask_admin_assistant(request: AdminPromptRequest):
    try:
        workloads = await ticket_service.get_employee_project_workloads(
            request.entityId,
            request.blockId,
        )
    except ticket_service.TicketApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The ATS API is temporarily unavailable.",
        ) from exc
    
    # 2. Assemble the prompt context
    full_prompt = prompt_service.format_admin_prompt(workloads, request.Prompt)
    
    # 3. Call LLM
    ai_response = await llm_service.call_llm_async(full_prompt)
    
    # 4. Handle success status
    success = not ai_response.startswith("Error")
    
    # 5. Return response matching exact schemas keys
    return AdminAssistantResponse(Success=success, Response=ai_response)
