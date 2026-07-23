from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class DashboardChatRequest(BaseModel):
    conversationId: Optional[str] = Field(default=None, description="Unique conversation session ID")
    message: str = Field(..., description="The user's query or message")
    userId: str = Field(..., description="The user UUID")
    entityId: str = Field(..., description="The entity UUID")
    blockId: str = Field(..., description="The block UUID")
    userRole: str = Field(..., description="The role of the user (e.g., Admin, User)")

class TicketCard(BaseModel):
    ticketId: str
    ticketNo: str
    title: str
    projectName: Optional[str] = None
    categoryName: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assignedToName: Optional[str] = None
    createdDate: Optional[str] = None
    dueDate: Optional[str] = None
    ageDays: Optional[int] = None

class TicketListData(BaseModel):
    kind: str = "ticket_list"
    totalCount: int
    pageIndex: int
    pageSize: int
    tickets: List[TicketCard]

class DashboardChatResponse(BaseModel):
    conversationId: str
    traceId: Optional[str] = Field(default=None, description="Trace ID for sanitized tool-call logs")
    type: str = Field(..., description="Response type: 'clarification' or 'answer'")
    message: str
    data: Optional[Dict[str, Any]] = Field(default=None, description="Structured payload like ticket list, counts, etc.")
    # suggestions: Optional[List[str]] = Field(default=None, description="Safe follow-up questions returned from tool data")
    sources: Optional[List[str]] = Field(default=None, description="Business areas used to answer the question")
    appliedFilters: Optional[Dict[str, Any]] = Field(default=None, description="Filters applied to the source data")

class FiltersState(BaseModel):
    status: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    priority: Optional[str] = None
    projectId: Optional[str] = None
    categoryId: Optional[str] = None
    assignedTo: Optional[str] = None

class ToolCallState(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)

class ConversationTurn(BaseModel):
    role: str
    text: str

class ConversationState(BaseModel):
    conversationId: str
    userId: str
    entityId: str
    blockId: str
    lastIntent: Optional[str] = None
    filters: FiltersState = Field(default_factory=FiltersState)
    pageIndex: int = 1
    pageSize: int = 30
    lastResultKind: Optional[str] = None
    lastTool: Optional[str] = None
    lastArgs: Dict[str, Any] = Field(default_factory=dict)
    lastResponseFormat: Optional[str] = None
    summary: str = ""
    recentTurns: List[ConversationTurn] = Field(default_factory=list)
