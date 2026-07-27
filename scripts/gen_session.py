#!/usr/bin/env python3
"""Одноразовая генерация пользовательской Telegram-сессии для парсера.

Создаёт `parser/session.session` — имя и каталог совпадают с тем, что ждёт
`parser/monitoring.py` (`Client(name="session", workdir=".../parser")`), поэтому
ручной `mv`/`chmod` больше не нужен.

`api_id`/`api_hash` передаются аргументами команды и зашиваются внутрь
`session.session`: в рантайме и в репозитории они не хранятся (`*.session` —
в `.gitignore`).

Запуск (в venv с установленными `kurigram` и `qrcode`):
    python gen_session.py <api_id> <api_hash>            # вход по QR (по умолчанию)
    python gen_session.py <api_id> <api_hash> --phone    # вход по телефону + коду
"""

import argparse
import os
import stat
import sys
from pathlib import Path

# Каталог parser/ относительно самого скрипта — сессия попадёт туда независимо
# от текущей рабочей директории.
PARSER_DIR = Path(__file__).resolve().parent / "parser"
SESSION_FILE = PARSER_DIR / "session.session"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Создать parser/session.session для парсера Survival Map.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="api_id/api_hash берутся на https://my.telegram.org/apps",
    )
    ap.add_argument("api_id", type=int, help="api_id с my.telegram.org/apps")
    ap.add_argument("api_hash", help="api_hash с my.telegram.org/apps")
    ap.add_argument(
        "--phone", action="store_true",
        help="вход по номеру телефона + коду (по умолчанию — по QR-коду)",
    )
    args = ap.parse_args()

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

    PARSER_DIR.mkdir(parents=True, exist_ok=True)

    app = Client(
        "session",
        api_id=args.api_id,
        api_hash=args.api_hash,
        workdir=str(PARSER_DIR),
    )

    if not args.phone:
        print("Вход по QR: Telegram → Настройки → Устройства → "
              "«Подключить устройство» → отсканируйте QR ниже.")

    # use_qr=True показывает QR в терминале (нужен пакет qrcode); --phone
    # переключает на интерактивный ввод телефона + кода (+ пароль 2FA).
    app.start(use_qr=not args.phone)
    me = app.get_me()
    app.stop()

    # Права 600 — секрет доступа к аккаунту не должен быть читаем другими.
    try:
        os.chmod(SESSION_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    handle = f"@{me.username}" if getattr(me, "username", None) else (me.first_name or "—")
    print(f"\n✅ Сессия создана: {SESSION_FILE} (вход как {handle})")
    print("   Права 600 выставлены. Теперь можно запускать docker compose up -d.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
