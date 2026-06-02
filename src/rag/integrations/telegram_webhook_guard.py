from __future__ import annotations

from collections import deque
from threading import Lock


class TelegramWebhookGuard:
    def __init__(self, max_processed_updates: int = 2048) -> None:
        self._max_processed_updates = max_processed_updates
        self._processed_update_ids: deque[int] = deque(maxlen=max_processed_updates)
        self._processed_update_ids_index: set[int] = set()
        self._processed_update_ids_lock = Lock()
        self._started_chats: set[int] = set()
        self._started_chats_lock = Lock()

    def is_duplicate_update(self, update_id: int) -> bool:
        with self._processed_update_ids_lock:
            if update_id in self._processed_update_ids_index:
                return True

            if len(self._processed_update_ids) >= self._max_processed_updates:
                removed = self._processed_update_ids.popleft()
                self._processed_update_ids_index.discard(removed)

            self._processed_update_ids.append(update_id)
            self._processed_update_ids_index.add(update_id)
            return False

    def is_first_start(self, chat_id: int) -> bool:
        with self._started_chats_lock:
            if chat_id in self._started_chats:
                return False
            self._started_chats.add(chat_id)
            return True


telegram_webhook_guard = TelegramWebhookGuard()
