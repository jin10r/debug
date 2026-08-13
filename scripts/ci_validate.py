#!/usr/bin/env python3
"""CI validation: architectural rules enforcement."""
import os
import re
import sys
from pathlib import Path

NLP_MODULES = {'pymorphy3', 'rapidfuzz', 'snowballstemmer', 'processor'}
NLP_CLASSES = {'GeoMatcher', 'LayerClassifier', 'Morphology', 'PhoneticIndex', 'SemanticMatcher'}

def check_nlp_isolation():
    violations = []
    for root in ['core', 'parser', 'web']:
        for path in Path(root).rglob('*.py'):
            if 'text_preprocessor.py' in str(path):
                continue
            content = path.read_text()
            for mod in NLP_MODULES:
                if f'import {mod}' in content or f'from {mod}' in content:
                    violations.append(f"{path}: forbidden NLP import '{mod}'")
            for cls in NLP_CLASSES:
                if re.search(rf'from\s+\S+\s+import\s+.*\b{cls}\b', content):
                    violations.append(f"{path}: forbidden NLP class '{cls}'")
    return violations

FORBIDDEN_BLOCKING = ['import requests', 'import psycopg2', 'time.sleep']

def check_no_blocking_calls():
    violations = []
    for root in ['core', 'parser', 'processor']:
        for path in Path(root).rglob('*.py'):
            content = path.read_text()
            for blocked in FORBIDDEN_BLOCKING:
                if blocked in content:
                    violations.append(f"{path}: blocking call '{blocked}'")
    return violations

SQL_FSTRING_RE = re.compile(r'f[\"\']\s*(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)\b', re.IGNORECASE)

def check_no_sql_fstrings():
    violations = []
    for path in Path('.').rglob('*.py'):
        if 'scripts/' in str(path):
            continue
        content = path.read_text()
        for match in SQL_FSTRING_RE.finditer(content):
            line = content[:match.start()].count('\n') + 1
            violations.append(f"{path}:{line}: SQL f-string detected")
    return violations

SECRETS_RE = re.compile(r'api_id|api_hash', re.IGNORECASE)

def check_no_telegram_secrets():
    violations = []
    for path in Path('.').rglob('*.py'):
        if str(path).startswith('scripts/'):
            continue
        content = path.read_text()
        if SECRETS_RE.search(content):
            violations.append(f"{path}: Telegram secret detected (api_id/api_hash)")
    return violations

def check_no_message_drops():
    violations = []
    processor_path = Path('processor/main.py')
    if processor_path.exists():
        content = processor_path.read_text()
        in_process_row = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('async def _process_row'):
                in_process_row = True
            elif in_process_row and stripped.startswith('async def '):
                in_process_row = False
            elif in_process_row and 'return None' in stripped:
                violations.append("processor/main.py: possible message drop in _process_row (return None)")
    return violations

def main():
    all_violations = []
    all_violations.extend(check_nlp_isolation())
    all_violations.extend(check_no_blocking_calls())
    all_violations.extend(check_no_sql_fstrings())
    all_violations.extend(check_no_telegram_secrets())
    all_violations.extend(check_no_message_drops())
    
    if all_violations:
        print("ARCHITECTURE RULE VIOLATIONS:")
        for v in all_violations:
            print(f"  - {v}")
        sys.exit(1)
    print("OK: All architecture rules passed")
    sys.exit(0)

if __name__ == '__main__':
    main()
