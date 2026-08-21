#!/usr/bin/env python3
"""Одноразовая генерация пользовательской Telegram-сессии для парсера.

Создаёт `parser/session.session` — имя и каталог совпадают с тем, что ждёт
`parser/monitoring.py` (`Client(name="session", workdir=".../parser")`), поэтому
ручной `mv`/`chmod` больше не нужен.

`api_id`/`api_hash` и номер телефона передаются через переменные окружения
(`PARSER_API_ID`, `PARSER_API_HASH`, `PARSER_PHONE`) и зашиваются внутрь
`session.session`: в репозитории они не хранятся (`*.session` — в `.gitignore`).

Запуск (в venv с установленными `kurigram` и `qrcode`):
    PARSER_API_ID=<id> PARSER_API_HASH=<hash> python gen_session.py                                # вход по QR
    PARSER_API_ID=<id> PARSER_API_HASH=<hash> PARSER_PHONE=+79991234567 python gen_session.py      # вход по телефону + коду
    SESSION_OUTPUT=/path/to/session.session python gen_session.py  # кастомный путь (по умолчанию — parser/session.session)
"""

import os
import stat
import sys
from pathlib import Path

# Каталог parser/ в корне проекта (родитель scripts/) — сессия попадёт туда
# независимо от текущей рабочей директории и того, откуда вызван скрипт.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PARSER_DIR = PROJECT_ROOT / "parser"
SESSION_FILE = PARSER_DIR / "session.session"


def main() -> int:
    api_id_str = os.environ.get("PARSER_API_ID")
    api_hash = os.environ.get("PARSER_API_HASH")
    phone = (os.environ.get("PARSER_PHONE") or "").strip()
    session_output = os.environ.get("SESSION_OUTPUT", str(SESSION_FILE))

    if not api_id_str or not api_hash:
        print(
            "ERROR: PARSER_API_ID и PARSER_API_HASH должны быть заданы через "
            "переменные окружения (G-15). Пример:\n"
            "  PARSER_API_ID=123 PARSER_API_HASH=abc... \\\n"
            "  PARSER_PHONE=+79991234567 SESSION_OUTPUT=parser/session.session \\\n"
            "  python scripts/gen_session.py",
            file=sys.stderr,
        )
        return 1

    try:
        api_id = int(api_id_str)
    except ValueError:
        print("ERROR: PARSER_API_ID должен быть целым числом", file=sys.stderr)
        return 1

    try:
        from pyrogram import Client  # модуль ставится пакетом kurigram
    except ImportError:
        print(
            "Не найден модуль pyrogram. Установите зависимости в venv:\n"
            "    python3 -m venv .venv && source .venv/bin/activate\n"
            "    pip install kurigram qrcode",
            file=sys.stderr,
        )
        return 1

    session_output_path = Path(session_output)
    output_dir = session_output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    app = Client(
        session_output_path.stem,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(output_dir),
        phone_number=phone if phone else None,
    )

    if not phone:
        print("Вход по QR: Telegram → Настройки → Устройства → "
              "«Подключить устройство» → отсканируйте QR ниже.")
    # use_qr=True показывает QR в терминале (нужен пакет qrcode); PARSER_PHONE
    # переключает на интерактивный ввод номера + кода (+ пароль 2FA).
    app.start(use_qr=not bool(phone))
    me = app.get_me()
    app.stop()

    # Права 600 — секрет доступа к аккаунту не должен быть читаем другими.
    try:
        os.chmod(session_output_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    handle = f"@{me.username}" if getattr(me, "username", None) else (me.first_name or "—")
    print(f"\n✅ Сессия создана: {session_output_path} (вход как {handle})")
    print("   Права 600 выставлены. Теперь можно запускать docker compose up -d.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
