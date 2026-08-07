# AI Ticket Assistant - Backend API

This is the FastAPI backend service for the AI Ticket Assistant. It establishes a connection to the SQL Server database `ATSDB` and uses LangChain with Ollama to answer user prompts about tickets, summarize description details, recommend ticket assignments, and query ASP.NET-backed dashboard tools.

## Technology Stack
* **Framework:** FastAPI (Python 3.12+)
* **Database Access:** SQLAlchemy + PyODBC
* **AI orchestration:** LangChain 1.x with `ChatOllama` and request-scoped `StructuredTool` wrappers
* **Model:** Ollama (`gemma4:cloud` by default)

## Configuration
Before running, ensure your `.env` file is set up:
```env
DATABASE_URL=mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+17+for+SQL+Server%7D%3BSERVER%3DDESKTOP-AIT044%3BDATABASE%3DATSDB%3BTrusted_Connection%3Dyes%3BTrustServerCertificate%3Dyes

# Local Ollama (uncomment these and comment the cloud values to run locally)
# OLLAMA_URL=http://localhost:11434
# OLLAMA_MODEL=gemma4:cloud

# Ollama Cloud API
OLLAMA_URL=https://ollama.com
OLLAMA_MODEL=gemma4:31b
OLLAMA_API_KEY=replace-with-your-ollama-api-key
OLLAMA_TIMEOUT_SECONDS=300
PORT=8000
```

Cloud mode sends `OLLAMA_API_KEY` as a Bearer token. Local mode does not need an API key; comment the three cloud variables and uncomment the two local variables. All model requests use the shared LangChain chat runnable in `app/services/llm_service.py`. Dashboard operations remain implemented by the existing ASP.NET APIs; `app/dashboard_chat/tool_registry.py` exposes them as validated LangChain tools while preserving the current user/entity/block scope and response format.

## Running the API Server
Run the following command from the workspace root or the `ai-ticket-assistant` directory:
```bash
.\myenv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## IIS + Docker deployment

The Windows IIS/Docker publish files are [documented here](DEPLOYMENT-IIS.md). The public API prefix is `https://apps.arizonixdigital.com/ATS/Ai/api/`.

## Endpoints

### POST `/api/assistant/ask`
Retrieves ticket and assignee context from the database, joins it with active employee records, formats a prompt, calls Ollama, and returns the response.

**Request body:**
```json
{
  "ticket_id": "CDD64C27-EBCC-4157-AD27-00022847DE5E",
  "prompt": "Summarize this ticket's description."
}
```

### POST `/api/assistant/employee/dashboard-chat`

Provides an employee-scoped chatbot for the current employee's tickets, ticket history, ticket counts, performance summary, ticket creation, and permitted ticket modifications. The endpoint verifies that `userId` belongs to an active Employee in the supplied entity/block and does not expose admin-only tools.

```powershell
$body = @{
    conversationId = $null
    message = "Show my closed tickets this month"
    userId = "352FDE24-B475-4FA2-B821-0328CC6AFE65"
    entityId = "11111111-1111-1111-1111-111111111111"
    blockId = "22222222-2222-2222-2222-222222222222"
    userRole = "Employee"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://localhost:8000/api/assistant/employee/dashboard-chat" `
    -ContentType "application/json" `
    -Body $body
```

The caller must populate the scope fields from the authenticated login session, not from editable user input. Admin requests continue to use `POST /api/assistant/admin/dashboard-chat` with `userRole` set to `Admin`.

### POST `/api/assistant/ticket-description/from-audio`

Uploads a short audio recording, automatically detects its spoken language, translates it to English with Faster-Whisper, and returns an Ollama-polished ticket-description draft. The endpoint does not create or update a ticket, and the temporary audio file is deleted after processing.

Send `multipart/form-data` with an `audio` file (MP3, M4A, OGG, WAV, or WebM). Optional fields are `language` (an ISO language code) and `project_context` (for product-specific terminology). The response contains both `translatedText` and the user-reviewable `polishedDescription`.

Configure local processing with:

```env
OLLAMA_MODEL=gemma4:cloud
WHISPER_MODEL=medium
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
MAX_AUDIO_BYTES=10485760
MAX_AUDIO_DURATION_SECONDS=120
```

**Response body:**
```json
{
  "text": "### Ticket Summary\nThis ticket involves calibration schedule sendback...",
  "status": "success"
}
```
