"""Message processor — preprocessing Telegram messages before inserting into pending_events.

Parser responsibility (R-P1): strip_tail → preprocess_light → batch INSERT INTO pending_events.
NLP pipeline (tokenize, lemmatize, classify, geo) is handled by processor service.
"""

import asyncio

from core.settings import settings
from core.utils.text_preprocessor import strip_tail, preprocess_light, is_promotional


class MessageProcessor:
    """Processes raw Telegram messages: preprocesses text and inserts into pending_events."""

    def __init__(self, db_pool):
        self._pool = db_pool
        self._pending_queue: asyncio.Queue = asyncio.Queue()
        self._batch_size = 50
        self._batch_interval = 0.5
        self._batch_task: asyncio.Task | None = None

    async def initialize(self) -> bool:
        self._batch_task = asyncio.create_task(self._batch_writer())
        return True

    async def process_message(self, msg_data: dict) -> dict | None:
        text = msg_data['text']
        message_id = msg_data['message_id']
        event_time = msg_data['event_time']

        cleaned = strip_tail(text)
        description = preprocess_light(cleaned)
        if len(description) > settings.parser.max_text_length:
            description = 'слишком длиннное сообщение, не является релевантной локацией'
        tokens = len(description.split())
        promo = is_promotional(description)

        await self._pending_queue.put({
            'message_id': message_id,
            'text': description,
            'event_time': event_time,
        })
        return {
            'event_id': None,
            'layer': 'pending',
            'strategy': 'random' if promo else 'pending',
            'geo_matched': 0,
            'tokens': tokens,
        }

    async def _batch_writer(self):
        while True:
            batch = []
            try:
                item = await asyncio.wait_for(
                    self._pending_queue.get(), timeout=self._batch_interval
                )
                batch.append(item)
                while len(batch) < self._batch_size:
                    try:
                        item = self._pending_queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                if not batch:
                    continue

            if batch:
                await self._write_batch(batch)
                for _ in batch:
                    self._pending_queue.task_done()

    async def _write_batch(self, batch: list[dict]):
        async with self._pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO pending_events (message_id, text, event_time) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (message_id, event_time) DO NOTHING",
                [(b['message_id'], b['text'], b['event_time']) for b in batch]
            )

    async def close(self):
        if self._batch_task and not self._batch_task.done():
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
