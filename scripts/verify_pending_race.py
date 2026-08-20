#!/usr/bin/env python3
"""Проверка двухфазного claim очереди pending_events (R-PR11).

Гоняет против живой PostgreSQL точный SQL из processor/main.py
(_fetch_pending, _requeue, _cleanup_stale_processing) при 8 конкурентных
воркерах с искусственной задержкой обработки 2 секунды и убеждается,
что одно и то же message_id никогда не обрабатывается параллельно.

Использование (из состава docker-сети с postgres):
    docker run --rm --network archive6_db --env-file .env \
        -v "$PWD/scripts":/app/scripts -w /app survival_core:latest \
        python scripts/verify_pending_race.py

Параметры берутся из env: POSTGRES_HOST/POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB.
Скрипт безопасен для повторов: сеет собственные строки (отрицательные
message_id) и удаляет их в конце.
"""

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("verify_pending_race")

N_WORKERS = 8
N_ROWS = 30
PROCESSING_DELAY_S = 2.0  # искусственная задержка обработки (как в задании)
# asyncpg кодирует timedelta в native interval — строку он не принимает.
STALE_INTERVAL = timedelta(minutes=5)
STALE_AGE = timedelta(minutes=6)

# --- SQL зеркалирует processor/main.py (R-PR11) -----------------------------

CLAIM_SQL = """
    UPDATE pending_events
    SET status = 'processing', locked_at = now(), worker_id = $1
    WHERE id = (
        SELECT id FROM pending_events
        WHERE status = 'pending'
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, message_id, text, event_time, photo_file_id
"""

DONE_SQL = """
    UPDATE pending_events
    SET status = 'done', processed_at = now(),
        locked_at = NULL, worker_id = NULL
    WHERE id = $1
"""

REQUEUE_SQL = """
    UPDATE pending_events
    SET status = 'pending', locked_at = NULL, worker_id = NULL
    WHERE id = $1 AND status = 'processing'
"""

CLEANER_SQL = """
    UPDATE pending_events
    SET status = 'pending', locked_at = NULL, worker_id = NULL
    WHERE status = 'processing' AND locked_at < now() - $1::interval
"""

# --- шаг 1: гонка воркеров ------------------------------------------------


class WorkerStat:
    """Счётчики одного воркера."""

    def __init__(self, name: str):
        self.name = name
        self.claimed = 0
        self.duplicate_claims = 0
        self.parallel_conflicts = 0


async def worker_routine(pool, stat, claims, active, report_lock):
    """Воркер: claim → 'обработка' (sleep) → done. Следит за гонками."""
    while True:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(CLAIM_SQL, stat.name)
        if row is None:
            return
        message_id = row["message_id"]
        async with report_lock:
            if message_id in active:
                stat.parallel_conflicts += 1
            if message_id in claims:
                stat.duplicate_claims += 1
            claims[message_id] = stat.name
            active.add(message_id)
            stat.claimed += 1
        # Искусственная задержка: в это время другие воркеры не должны
        # захватить ту же задачу (status уже 'processing').
        await asyncio.sleep(PROCESSING_DELAY_S)
        async with pool.acquire() as conn:
            await conn.execute(DONE_SQL, row["id"])
        async with report_lock:
            active.discard(message_id)


async def run_race_test(pool) -> bool:
    """Сев 30 задач и прогнать 8 воркеров. Возвращает PASS/FAIL."""
    offset = uuid.uuid4().int % (10 ** 9)
    message_ids = [-(offset + i) for i in range(N_ROWS)]
    now = datetime.now(timezone.utc)
    rows = [
        (mid, f"verify-race-{offset}-{i}",
         now - timedelta(days=365) + timedelta(seconds=i))
        for i, mid in enumerate(message_ids)
    ]

    try:
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO pending_events (message_id, text, event_time) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (message_id, event_time) DO NOTHING",
                rows,
            )
            inserted = await conn.fetchval(
                "SELECT count(*) FROM pending_events "
                "WHERE message_id = ANY($1::bigint[])",
                message_ids,
            )
        if inserted != N_ROWS:
            logger.error("Seed failed: inserted %d/%d rows (offset collision?)",
                         inserted, N_ROWS)
            return False
        logger.info("Seeded %d rows (message_ids from %s)", N_ROWS, message_ids[0])

        claims = {}
        active = set()
        report_lock = asyncio.Lock()
        stats = [WorkerStat(f"w{i}") for i in range(N_WORKERS)]

        t0 = datetime.now(timezone.utc)
        await asyncio.gather(
            *(worker_routine(pool, s, claims, active, report_lock) for s in stats)
        )
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

        for s in sorted(stats, key=lambda x: x.name):
            logger.info(
                "  %s: claimed=%d duplicates=%d parallel_conflicts=%d",
                s.name, s.claimed, s.duplicate_claims, s.parallel_conflicts,
            )

        total_claimed = sum(s.claimed for s in stats)
        unique = len(claims)
        async with pool.acquire() as conn:
            statuses = dict(
                (r["status"], r["count"])
                for r in await conn.fetch(
                    "SELECT status, count(*) AS count FROM pending_events "
                    "WHERE message_id = ANY($1::bigint[]) GROUP BY status",
                    message_ids,
                )
            )

        ok = True
        logger.info("Race test: claimed=%d unique=%d duplicates=%d time=%.1fs",
                    total_claimed, unique, total_claimed - unique, elapsed)
        if total_claimed != N_ROWS:
            logger.error("Not all rows claimed: %d/%d", total_claimed, N_ROWS)
            ok = False
        if unique != N_ROWS:
            logger.error("Duplicate claims detected: %d unique of %d claimed",
                         unique, total_claimed)
            ok = False
        if any(s.parallel_conflicts for s in stats):
            logger.error("Parallel processing of the same message_id detected")
            ok = False
        if statuses.get("done") != N_ROWS:
            logger.error("Final statuses wrong: %s", statuses)
            ok = False
        return ok
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pending_events WHERE message_id = ANY($1::bigint[])",
                message_ids,
            )
        logger.info("Race rows cleaned up")


# --- шаг 2: очиститель зависших задач + guard в requeue -------------------


async def run_cleaner_test(pool) -> bool:
    """Строка 'processing' старше 5 минут возвращается в 'pending'.

    Тест детерминирован: не зависит от состояния очереди — работаем только
    со своей строкой через прямой UPDATE status (обход CLAIM).
    """
    message_id = -(uuid.uuid4().int % (10 ** 9))
    now = datetime.now(timezone.utc)
    inserted_id = None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO pending_events (message_id, text, event_time) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (message_id, event_time) DO NOTHING "
                "RETURNING id",
                message_id, "verify-cleaner", now - timedelta(days=365),
            )
            inserted_id = row["id"]
            # Имитация зависшей задачи воркера: processing + stale locked_at.
            await conn.execute(
                "UPDATE pending_events "
                "SET status = 'processing', locked_at = now() - $1::interval, "
                "    worker_id = 'cleaner-test' "
                "WHERE id = $2",
                STALE_AGE, inserted_id,
            )
            status = await conn.execute(CLEANER_SQL, STALE_INTERVAL)
            cleaned = int(status.split()[-1]) if status else 0
            state = await conn.fetchval(
                "SELECT status FROM pending_events WHERE id = $1", inserted_id
            )

        ok = cleaned == 1 and state == "pending"
        logger.info("Cleaner test: stale rows reset=%d state=%s", cleaned, state)
        if not ok:
            logger.error("Stale cleaner failed")

        # Guard в _requeue: 'done' задача не должна вернуться в 'pending'.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_events SET status = 'done' WHERE id = $1",
                inserted_id,
            )
            await conn.execute(REQUEUE_SQL, inserted_id)
            state2 = await conn.fetchval(
                "SELECT status FROM pending_events WHERE id = $1", inserted_id
            )

        ok2 = state2 == "done"
        logger.info("Requeue guard test: done row status=%s (must stay 'done')", state2)
        if not ok2:
            logger.error("Requeue guard failed: done task was requeued")
        return ok and ok2
    finally:
        if inserted_id is not None:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM pending_events WHERE id = $1", inserted_id
                )


async def main() -> int:
    """Точка входа."""
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "postgres")

    pool = await asyncpg.create_pool(
        host=host, port=port, user=user, password=password,
        database=database, min_size=N_WORKERS, max_size=32,
        command_timeout=60,
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute("ALTER TABLE pending_events ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ")
            await conn.execute("ALTER TABLE pending_events ADD COLUMN IF NOT EXISTS worker_id TEXT")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_events_stale "
                "ON pending_events(locked_at) WHERE status = 'processing'"
            )
        logger.info("Schema ensured (locked_at/worker_id)")

        logger.info(
            "RACE TEST: %d workers, %d rows, %.0fs artificial delay",
            N_WORKERS, N_ROWS, PROCESSING_DELAY_S,
        )
        race_ok = await run_race_test(pool)
        cleaner_ok = await run_cleaner_test(pool)

        if race_ok and cleaner_ok:
            logger.info("ALL CHECKS PASSED: race test=%s cleaner test=%s",
                        race_ok, cleaner_ok)
            return 0
        logger.error("FAILED: race test=%s cleaner test=%s", race_ok, cleaner_ok)
        return 1
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))