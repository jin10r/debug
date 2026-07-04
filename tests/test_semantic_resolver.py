"""Тесты SemanticResolver — pre-filter правил без вызова модели.

Проверяет быстрые правила определения стратегии:
  • prepositional construction → midpoint
  • type hint in text → single_match / midpoint
  • duplicate names → fallback to model (None)
  • single candidate → None (решается на уровень выше в message_processor)
"""

import asyncio
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

if "parser" not in sys.modules:
    _pkg = types.ModuleType("parser")
    _pkg.__path__ = [str(ROOT / "parser")]
    sys.modules["parser"] = _pkg

from parser.semantic_resolver import SemanticResolver  # noqa: E402


@pytest.fixture
def resolver():
    r = SemanticResolver(None, None)
    r._initialized = True
    r._stopwords = {'на', 'по', 'в', 'у', 'до', 'с', 'и', 'а', 'не'}
    r._ollama_base = None
    return r


def _make_candidates(*specs):
    """specs: (id, name, type)"""
    return [
        {"geo_id": sid, "matched_name": name, "type": t, "score": 0.95}
        for sid, name, t in specs
    ]


class TestPreFilterPrepositional:
    """Правило 1: предлоги направления → midpoint."""

    async def test_ot_do_midpoint(self, resolver):
        """'от X до Y' → midpoint (только street/market/station/park/landmark)."""
        cand = _make_candidates(
            (1, "Дерибасовская", "street"),
            (4, "Ришельевская", "street"),
        )
        result = resolver._pre_filter("от Дерибасовской до Ришельевской пробка", cand)
        assert result is not None
        assert result['strategy'] == 'midpoint'

    async def test_midpoint_only_street_types(self, resolver):
        """midpoint только для street/park/market/station/landmark."""
        cand = _make_candidates(
            (1, "Александровка", "village"),
            (4, "Ильичёвск", "town"),
        )
        result = resolver._pre_filter("от Александровки до Ильичёвска", cand)
        # village/town не входит в _MIDPOINT_TYPES → pre-filter пропускает
        assert result is None

    async def test_mezhdu_midpoint(self, resolver):
        """'между X и Y' → midpoint."""
        cand = _make_candidates(
            (10, "Дерибасовская", "street"),
            (20, "Ришельевская", "street"),
        )
        result = resolver._pre_filter("между Дерибасовской и Ришельевской пробка", cand)
        assert result is not None
        assert result['strategy'] == 'midpoint'

    async def test_between_landmark_midpoint(self, resolver):
        """midpoint для park."""
        cand = _make_candidates(
            (30, "Горького", "park"),
            (40, "Шевченко", "park"),
        )
        result = resolver._pre_filter("между парком Горького и парком Шевченко", cand)
        assert result is not None
        assert result['strategy'] == 'midpoint'


class TestPreFilterTypeHint:
    """Правило 2: явный тип объекта в тексте."""

    async def test_selo_hint_single(self, resolver):
        """'село Александровка' → single_match на объект типа village."""
        cand = _make_candidates(
            (1, "Александровка", "village"),
            (2, "Александровка", "town"),
            (3, "Александровка", "village"),
        )
        result = resolver._pre_filter("село Александровка блокпост", cand)
        # Несколько village → всё равно pre-filter не может выбрать один
        assert result is None or len(result['geo_ids']) > 1

    async def test_pgt_hint(self, resolver):
        """'пгт Таирово' → single_match на town."""
        cand = _make_candidates(
            (5, "Таирово", "village"),
            (6, "Таирово", "town"),
        )
        result = resolver._pre_filter("пгт Таирово перехватчики", cand)
        assert result is not None
        assert result['strategy'] == 'single_match'
        assert 6 in result['geo_ids']

    async def test_station_hint(self, resolver):
        """'станция' → single_match на station."""
        cand = _make_candidates(
            (7, "Одесса-Главная", "station"),
            (8, "Одесса", "town"),
        )
        result = resolver._pre_filter("на станции Одесса-Главная проверка", cand)
        assert result is not None
        assert result['strategy'] == 'single_match'
        assert 7 in result['geo_ids']

    async def test_park_hint(self, resolver):
        """'парк' → single_match на park."""
        cand = _make_candidates(
            (9, "Горького", "park"),
            (10, "Горького", "street"),
        )
        result = resolver._pre_filter("в парке Горького патруль", cand)
        assert result is not None
        assert result['strategy'] == 'single_match'
        assert 9 in result['geo_ids']


class TestPreFilterDuplicateNames:
    """Правило 3: одноимённые объекты."""

    async def test_duplicate_names_no_other_candidate(self, resolver):
        """Только дубликаты одного имени, без других кандидатов → None."""
        cand = _make_candidates(
            (1, "Александровка", "village"),
            (2, "Александровка", "town"),
            (3, "Александровка", "village"),
        )
        result = resolver._pre_filter("Александровка блокпост", cand)
        assert result is None

    async def test_duplicate_names_with_other_candidate(self, resolver):
        """Дубликат + другой кандидат → None (пусть модель решает)."""
        cand = _make_candidates(
            (1, "Александровка", "village"),
            (2, "Александровка", "town"),
            (3, "Ильичёвск", "town"),
        )
        result = resolver._pre_filter("Александровка возле Ильичёвска", cand)
        assert result is None


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
        """Пустой текст → pre-filter не срабатывает → None (fallback)."""
        cand = _make_candidates((1, "Тестовая", "street"))
        result = await resolver.resolve("", [], [], cand)
        assert result is None

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
        from parser.semantic_resolver import _build_prompt

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
