# AI Ticket Assistant - Backend API

This is the FastAPI backend service for the AI Ticket Assistant. It establishes a connection to the SQL Server database `ATSDB` and uses the local Ollama LLM to answer user prompts about tickets, summarize description details, and recommend ticket assignments.

## Technology Stack
* **Framework:** FastAPI (Python 3.12+)
* **Database Access:** SQLAlchemy + PyODBC
* **LLM Integration:** Ollama (`gemma4:12b-it-q4_K_M` model)

## Configuration
Before running, ensure your `.env` file is set up:
```env
DATABASE_URL=mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+17+for+SQL+Server%7D%3BSERVER%3DDESKTOP-AIT044%3BDATABASE%3DATSDB%3BTrusted_Connection%3Dyes%3BTrustServerCertificate%3Dyes
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:12b-it-q4_K_M
PORT=8000
```

## Running the API Server
Run the following command from the workspace root or the `ai-ticket-assistant` directory:
```bash
.\myenv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

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

**Response body:**
```json
{
  "text": "### Ticket Summary\nThis ticket involves calibration schedule sendback...",
  "status": "success"
}
```
