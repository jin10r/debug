"""BatchProcessor — asyncio-based batching for CPU-efficient LLM inference.

Собирает запросы от воркеров в батч до `batch_size` или до `timeout_ms`,
затем отправляет одним вызовом в LLMBackend.infer_batch().
"""

import asyncio
import logging
from time import monotonic
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Batching proxy for LLM inference.

    Workers call `submit(msg_id, data)` which returns an awaitable Future.
    The batcher coroutine collects items from an asyncio.Queue and flushes
    them when batch_size is reached or batch_timeout_ms elapses.

    Example:
        bp = BatchProcessor(llm_backend.infer_batch, batch_size=8, timeout_ms=50)
        asyncio.create_task(bp.run())
        result = await bp.submit(msg_id, {"messages": [...], "grammar": "unified"})
    """

    def __init__(
        self,
        infer_fn: Callable[..., List[Optional[Dict[str, Any]]]],
        batch_size: int = 8,
        timeout_ms: int = 50,
        grammar_name: str = 'unified',
    ) -> None:
        self._infer_fn = infer_fn
        self._batch_size = batch_size
        self._timeout_s = timeout_ms / 1000.0
        self._grammar_name = grammar_name

        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending: Dict[int, asyncio.Future] = {}
        self._seq = 0
        self._running = False

    async def submit(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Submit a single item for batched inference. Returns the result."""
        msg_id = self._seq
        self._seq += 1
        future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future
        await self._queue.put((msg_id, data))
        return await future

    async def run(self) -> None:
        """Main batcher loop. Run as a background task."""
        self._running = True
        logger.info(
            f"[Batch] Started: batch_size={self._batch_size}, "
            f"timeout={self._timeout_s*1000:.0f}ms"
        )

        while self._running:
            batch: List[tuple] = []
            deadline = monotonic() + self._timeout_s

            # Collect until batch full or timeout
            try:
                while len(batch) < self._batch_size:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    item = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=remaining,
                    )
                    batch.append(item)
            except asyncio.TimeoutError:
                pass

            if not batch:
                await asyncio.sleep(0.001)
                continue

            # Process batch in thread pool
            batch_data = [data for _, data in batch]
            try:
                batch_results = await asyncio.to_thread(
                    self._infer_fn,
                    batch_data,
                    self._grammar_name,
                )
            except Exception as e:
                logger.error(f"[Batch] Inference failed: {e}")
                batch_results = [None] * len(batch)

            # Dispatch results
            for (msg_id, _), result in zip(batch, batch_results):
                future = self._pending.pop(msg_id, None)
                if future is not None and not future.done():
                    future.set_result(result)

            logger.debug(
                f"[Batch] Flushed {len(batch)} items, "
                f"{self._queue.qsize()} queued"
            )

        logger.info("[Batch] Stopped")

    def stop(self) -> None:
        self._running = False
        # Cancel all pending futures
        for msg_id, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
        self._pending.clear()
