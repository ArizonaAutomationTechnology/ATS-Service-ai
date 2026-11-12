from typing import Optional

from pydantic import BaseModel, Field

class PromptRequest(BaseModel):
    ticket_id: str = Field(..., description="The UUID of the ticket as a string")
    prompt: str = Field(..., description="The user's prompt or request for the assistant")
    entityId: str = Field(..., description="The GUID of the Entity")
    blockId: str = Field(..., description="The GUID of the Block")

class AssistantResponse(BaseModel):
    text: str = Field(..., description="The generated response text from the AI Assistant")
    status: str = Field("success", description="The status of the request (e.g., success, error)")

class AdminPromptRequest(BaseModel):
    Prompt: str = Field(..., description="The admin's prompt asking about employee workloads")
    entityId: str = Field(..., description="The GUID of the Entity")
    blockId: str = Field(..., description="The GUID of the Block")

class AdminAssistantResponse(BaseModel):
    Success: bool = Field(..., description="Success flag")
    Response: str = Field(..., description="The AI's answer response string")


class SimilarTicketsRequest(BaseModel):
    entityId: str = Field(..., description="The GUID of the Entity")
    blockId: str = Field(..., description="The GUID of the Block")
    clientId: str = Field(..., description="The GUID of the Client")
    projectId: str = Field(..., description="The GUID of the Project")
    description: str = Field(..., description="The user's ticket description")


class SimilarTicketMatch(BaseModel):
    ticketId: str = Field(..., description="The GUID of the similar ticket")
    ticketNo: str = Field(..., description="The ticket number")
    status: str = Field(..., description="The status of the ticket")
    priority: str = Field(..., description="The priority of the ticket")
    description: str = Field(..., description="The description of the ticket")
    similarity: float = Field(..., description="Similarity score between 0.0 and 1.0")
    reason: str = Field(..., description="Reason for matching")


class SimilarTicketsResponse(BaseModel):
    hasSimilarTickets: bool = Field(..., description="Whether similar tickets were found")
    matches: list[SimilarTicketMatch] = Field(..., description="List of similar ticket matches")


class AudioDescriptionResponse(BaseModel):
    status: str = Field("success", description="success or partial_success")
    detectedLanguage: str = Field(..., description="Language detected by Faster-Whisper")
    languageConfidence: float = Field(..., description="Detected-language confidence from 0 to 1")
    translatedText: str = Field(..., description="English text translated from the recording")
    polishedDescription: Optional[str] = Field(
        None,
        description="AI-polished ticket description. Null when polishing is unavailable.",
    )
    warnings: list[str] = Field(default_factory=list, description="Non-blocking processing warnings")
