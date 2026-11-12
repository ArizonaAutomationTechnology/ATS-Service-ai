# AI Ticket Assistant - Backend API

This is the FastAPI backend service for the AI Ticket Assistant. It uses LangChain with the Gemini API to answer user prompts about tickets, summarize descriptions, recommend ticket assignments, and query ASP.NET-backed dashboard tools.

## Technology Stack
* **Framework:** FastAPI (Python 3.12+)
* **Database Access:** SQLAlchemy + PyODBC
* **AI orchestration:** LangChain 1.x with `ChatGoogleGenerativeAI` and request-scoped `StructuredTool` wrappers
* **Model:** Gemma 4 31B (`gemma-4-31b-it`) on the Gemini Developer API

## Configuration
Before running, ensure your `.env` file is set up:
```env
DATABASE_URL=mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+17+for+SQL+Server%7D%3BSERVER%3DDESKTOP-AIT044%3BDATABASE%3DATSDB%3BTrusted_Connection%3Dyes%3BTrustServerCertificate%3Dyes

GEMINI_API_KEY=replace-with-your-google-ai-studio-api-key
GEMINI_MODEL=gemma-4-31b-it
GEMINI_TIMEOUT_SECONDS=300
GEMINI_DESCRIPTION_TIMEOUT_SECONDS=90
PORT=8000
```

Create `GEMINI_API_KEY` in [Google AI Studio](https://aistudio.google.com/app/apikey). Google lists Gemma 4 input and output as free of charge, subject to project quotas. See [Gemma 4 pricing](https://ai.google.dev/gemini-api/docs/pricing#gemma-4) and [Gemini API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

Google's unpaid-service terms prohibit sending sensitive, confidential, or personal information. ATS prompts can include ticket details, employee information, and translated transcripts. Use synthetic records for free-tier testing. See [Gemini API terms](https://ai.google.dev/gemini-api/docs/terms#unpaid-services).

All model requests use the shared LangChain client in `app/services/llm_service.py`. The dashboard executes its request-scoped tools through the existing ASP.NET APIs.

## Running the API Server
Run these commands from the service directory with your virtual environment activated.
```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## IIS + Docker deployment

The Windows IIS/Docker publish files are [documented here](DEPLOYMENT-IIS.md). The public API prefix is `https://apps.arizonixdigital.com/ATS/Ai/api/`.

## Endpoints

### POST `/api/assistant/ask`
Retrieves ticket and assignee context, formats a prompt, calls the Gemini API, and returns the response.

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

Uploads a short audio recording, automatically detects its spoken language, translates it to English with Faster-Whisper, and returns a ticket description draft polished by Gemma 4. The endpoint returns a draft for review and deletes the temporary audio file after processing.

Send `multipart/form-data` with an `audio` file (MP3, M4A, OGG, WAV, or WebM). Optional fields are `language` (an ISO language code) and `project_context` (for product-specific terminology). The response contains both `translatedText` and the user-reviewable `polishedDescription`.

Configure local processing with:

```env
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
