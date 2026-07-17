"""Message processor — preprocessing Telegram messages before inserting into pending_events.

Parser responsibility (R-P1): strip_tail → preprocess_light → INSERT INTO pending_events.
NLP pipeline (tokenize, lemmatize, classify, geo) is handled by processor service.
"""

from .text_preprocessor import strip_tail, preprocess_light, is_promotional


class MessageProcessor:
    """Processes raw Telegram messages: preprocesses text and inserts into pending_events."""

    def __init__(self, db_pool):
        self._pool = db_pool

    async def initialize(self) -> bool:
        return True

    async def process_message(self, msg_data: dict) -> dict | None:
        text = msg_data['text']
        message_id = msg_data['message_id']
        event_time = msg_data['event_time']

        cleaned = strip_tail(text)
        description = preprocess_light(cleaned)
        tokens = len(description.split())
        promo = is_promotional(description)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO pending_events (message_id, text, event_time) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (message_id, event_time) DO NOTHING "
                "RETURNING id",
                message_id, description, event_time,
            )

        if row is None:
            return None

        return {
            'event_id': row['id'],
            'layer': 'pending',
            'strategy': 'random' if promo else 'pending',
            'geo_matched': 0,
            'tokens': tokens,
        }

    async def close(self):
        pass
