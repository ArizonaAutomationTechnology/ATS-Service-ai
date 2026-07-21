import threading
from typing import Dict, Optional
from app.dashboard_chat.schemas import ConversationState

class ConversationStateStore:
    def __init__(self):
        self._states: Dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._states.get(conversation_id)

    def set(self, conversation_id: str, state: ConversationState) -> None:
        with self._lock:
            self._states[conversation_id] = state

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            if conversation_id in self._states:
                del self._states[conversation_id]

# Global state store instance for the application
state_store = ConversationStateStore()
