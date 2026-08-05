"""Тесты SemanticResolver — правила определения стратегии геолокации.

Актуальный контракт (упрощённый резолвер, см. processor/semantic_resolver.py):
  • 'от X до Y' или 'между X и Y' при ≥2 кандидатах → midpoint;
  • всё остальное (type hints 'село/пгт/станция/парк', дубликаты имён,
    single match) резолвится в PostGIS (process_candidates) и здесь
    возвращает None.
"""

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# processor/__init__.py тянет тяжёлые зависимости (rapidfuzz/pymorphy3) —
# подменяем пакет лёгким стабом, чтобы импортировать semantic_resolver напрямую.
if "processor" not in sys.modules:
    _pkg = types.ModuleType("processor")
    _pkg.__path__ = [str(ROOT / "processor")]
    sys.modules["processor"] = _pkg

from processor.semantic_resolver import SemanticResolver  # noqa: E402


@pytest.fixture
def resolver():
    """Резолвер с выставленным флагом инициализации (без реальных БД/индексов)."""
    r = SemanticResolver(None, None)
    r._initialized = True
    return r


def _make_candidates(*specs):
    """specs: (id, name, type)"""
    return [
        {"geo_id": sid, "matched_name": name, "type": t, "score": 0.95}
        for sid, name, t in specs
    ]


class TestPreFilterPrepositional:
    """Правило: предлоги направления → midpoint (при ≥2 кандидатах)."""

    def test_ot_do_midpoint(self, resolver):
        """'от X до Y' → midpoint."""
        cand = _make_candidates(
            (1, "Дерибасовская", "street"),
            (4, "Ришельевская", "street"),
        )
        result = resolver._pre_filter("от Дерибасовской до Ришельевской пробка", cand)
        assert result is not None
        assert result['strategy'] == 'midpoint'
        assert result['geo_ids'] == [1, 4]

    def test_mezhdu_midpoint(self, resolver):
        """'между X и Y' → midpoint."""
        cand = _make_candidates(
            (10, "Дерибасовская", "street"),
            (20, "Ришельевская", "street"),
        )
        result = resolver._pre_filter("между Дерибасовской и Ришельевской пробка", cand)
        assert result is not None
        assert result['strategy'] == 'midpoint'

    def test_single_candidate_no_preposition(self, resolver):
        """Один кандидат без предлогов → None (решается в PostGIS)."""
        cand = _make_candidates((1, "Дерибасовская", "street"))
        assert resolver._pre_filter("Дерибасовская перекрыта", cand) is None

    def test_no_direction_preposition(self, resolver):
        """Без от/до/между → None, даже при нескольких кандидатах."""
        cand = _make_candidates(
            (1, "Дерибасовская", "street"),
            (4, "Ришельевская", "street"),
        )
        assert resolver._pre_filter("пробка на Дерибасовской и Ришельевской", cand) is None

    def test_between_landmark_midpoint(self, resolver):
        """midpoint работает и для не-street типов (парки и т.п.)."""
        cand = _make_candidates(
            (30, "Горького", "park"),
            (40, "Шевченко", "park"),
        )
        result = resolver._pre_filter("между парком Горького и парком Шевченко", cand)
        assert result is not None
        assert result['strategy'] == 'midpoint'


class TestResolveIntegration:
    """Интеграционные проверки полного цикла resolve()."""

    async def test_not_initialized(self, resolver):
        resolver._initialized = False
        result = await resolver.resolve("текст", [], [], [{"geo_id": 1}])
        assert result is None

    async def test_no_candidates(self, resolver):
        result = await resolver.resolve("текст", [], [], [])
        assert result is None

    async def test_empty_text(self, resolver):
        """Пустой текст → pre-filter не срабатывает → None (fallback в PostGIS)."""
        cand = _make_candidates((1, "Тестовая", "street"))
        result = await resolver.resolve("", [], [], cand)
        assert result is None

    async def test_midpoint_via_resolve(self, resolver):
        """Полный цикл: предлоги → midpoint."""
        cand = _make_candidates(
            (1, "Ланжероновская", "street"),
            (2, "Дерибасовская", "street"),
        )
        result = await resolver.resolve(
            "от Ланжероновской до Дерибасовской", [], [], cand
        )
        assert result is not None
        assert result['strategy'] == 'midpoint'

    async def test_unknown_strategy_not_possible(self, resolver):
        """pre-filter не возвращает неизвестных стратегий."""
        cand = _make_candidates(
            (1, "Ланжероновская", "street"),
            (2, "Дерибасовская", "street"),
        )
        result = resolver._pre_filter("от Ланжероновской до Дерибасовской", cand)
        assert result is None or result['strategy'] in ('single_match', 'intersection', 'midpoint')


class TestBuildPrompt:
    """Проверка формирования промпта (только структура)."""

    def test_prompt_contains_text_and_candidates(self):
        from processor.semantic_resolver import _build_prompt

        prompt = _build_prompt("Александровка блокпост", [
            {"geo_id": 1, "matched_name": "Александровка", "type": "village"},
            {"geo_id": 2, "matched_name": "Ильичёвск", "type": "town"},
        ])
        assert "Александровка" in prompt
        assert "1" in prompt
        assert "village" in prompt
        assert "single_match" in prompt
        assert "intersection" in prompt
        assert "midpoint" in prompt
        assert "json" in prompt.lower()
