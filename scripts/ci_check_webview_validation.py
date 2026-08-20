#!/usr/bin/env python3
"""CI-check: Secure by Default логика TELEGRAM_WEBVIEW_VALIDATION.

Используется матричным job'ом test:core-startup-matrix в .gitlab-ci.yml.
Проверяет, что load_settings корректно парсит переменную (True по умолчанию,
False только при 'false'/'0') и что при dev-bypass логируется "SECURITY RISK".
"""
import sys
import os
import logging
import tempfile

log_output = []


class ListHandler(logging.Handler):
    def emit(self, record):
        log_output.append(self.format(record))


logging.getLogger().addHandler(ListHandler())
logging.getLogger().setLevel(logging.WARNING)


def main():
    if len(sys.argv) != 4:
        print(
            "Usage: ci_check_webview_validation.py <TEST_ENV_VALUE> "
            "<EXPECTED_BOOL> <EXPECTED_LOG_WARNING>"
        )
        sys.exit(1)

    test_env_value = sys.argv[1]
    expected_bool = sys.argv[2].lower() == "true"
    expected_log_warning = sys.argv[3].lower() == "true"

    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        env_path = f.name
        if test_env_value != "UNSET":
            f.write(f"TELEGRAM_WEBVIEW_VALIDATION={test_env_value}\n")
        else:
            f.write("# TELEGRAM_WEBVIEW_VALIDATION is intentionally unset\n")

    try:
        from core.settings import load_settings
        # Важно: передаем env_path и получаем объект настроек, не полагаясь
        # на module-level singleton
        settings_obj = load_settings(env_path=env_path, require_jwt=False)

        actual_bool = settings_obj.app.telegram_webview_validation
        has_warning = any(
            "SECURITY RISK" in msg and "TELEGRAM_WEBVIEW_VALIDATION" in msg
            for msg in log_output
        )

        print(f"CHECK: Expected bool={expected_bool}, Actual={actual_bool}")
        print(f"CHECK: Expected warning={expected_log_warning}, Actual={has_warning}")

        if actual_bool != expected_bool or has_warning != expected_log_warning:
            print("FAIL: Secure by Default logic is broken!")
            sys.exit(1)
        print("PASS: Logic is correct.")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: Exception during check: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(env_path):
            os.remove(env_path)


if __name__ == "__main__":
    main()