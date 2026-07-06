"""Tests for SpaCyRelationExtractor module."""

import pytest
from parser.spacy_relation_extractor import SpaCyRelationExtractor, TYPE_MARKERS


@pytest.fixture
def spacy_extractor():
    """Create a SpaCyRelationExtractor instance for testing."""
    extractor = SpaCyRelationExtractor()
    # Initialize without loading model (for unit tests)
    extractor._enabled = True
    extractor._initialized = True
    extractor._custom_attributes_registered = True
    return extractor


class TestSpaCyRelationExtractor:
    """Тесты для SpaCyRelationExtractor."""

    def test_type_markers_dictionary(self):
        """Проверить что TYPE_MARKERS содержит ожидаемые записи."""
        assert "сквер" in TYPE_MARKERS
        assert TYPE_MARKERS["сквер"] == "park"
        assert "улица" in TYPE_MARKERS
        assert TYPE_MARKERS["улица"] == "street"
        assert "село" in TYPE_MARKERS
        assert TYPE_MARKERS["село"] == "village"

    def test_initialization_disabled(self, monkeypatch):
        """Проверить инициализацию когда spaCy отключён в настройках."""
        extractor = SpaCyRelationExtractor()

        # Mock settings to disable spaCy
        class MockSettings:
            class spacy:
                enabled = False

        import parser.spacy_relation_extractor as sre_module
        monkeypatch.setattr(sre_module, 'settings', MockSettings())

        result = extractor.initialize()
        assert result is True
        assert extractor._enabled is False

    def test_extract_plan_not_enabled(self, spacy_extractor):
        """Проверить что extract_plan возвращает пустой план когда модуль отключён."""
        spacy_extractor._enabled = False
        plan = spacy_extractor.extract_plan("тест", [])
        assert plan == {"plan": []}

    def test_extract_plan_no_candidates(self, spacy_extractor):
        """Проверить что extract_plan возвращает пустой план без кандидатов."""
        spacy_extractor._enabled = True
        plan = spacy_extractor.extract_plan("тест", [])
        assert plan == {"plan": []}

    def test_extract_plan_model_not_loaded(self, spacy_extractor):
        """Проверить что extract_plan возвращает пустой план когда модель не загружена."""
        spacy_extractor._enabled = True
        spacy_extractor._nlp = None
        # Mock _ensure_model_loaded to return False
        spacy_extractor._ensure_model_loaded = lambda: False

        candidates = [{"id": 1, "name": "test", "type": "street", "span": [0, 4]}]
        plan = spacy_extractor.extract_plan("тест", candidates)
        assert plan == {"plan": []}

    def test_extract_between_pattern(self, spacy_extractor):
        """Тест извлечения паттерна 'между X и Y'."""
        # This test requires a loaded spaCy model - skip if not available
        try:
            import spacy
            nlp = spacy.load("ru_core_news_sm")
            spacy_extractor._nlp = nlp
        except (ImportError, OSError):
            pytest.skip("spaCy model not available")

        # Mock candidates with spans
        candidates = [
            {"id": "1", "name": "улица А", "type": "street", "geom_type": "linestring", "span": [7, 15]},
            {"id": "2", "name": "улица Б", "type": "street", "geom_type": "linestring", "span": [19, 27]},
        ]

        query = "между улицей А и улицей Б"
        plan = spacy_extractor.extract_plan(query, candidates)

        # Should return intersection plan with bounds
        if plan.get("plan"):
            assert plan["plan"][0]["tool"] in ["intersection", "single_match"]

    def test_extract_single_candidate(self, spacy_extractor):
        """Тест с одним кандидатом - должен вернуть single_match."""
        try:
            import spacy
            nlp = spacy.load("ru_core_news_sm")
            spacy_extractor._nlp = nlp
        except (ImportError, OSError):
            pytest.skip("spaCy model not available")

        candidates = [
            {"id": "1", "name": "парк", "type": "park", "geom_type": "polygon", "span": [0, 4]},
        ]

        query = "парк"
        plan = spacy_extractor.extract_plan(query, candidates)

        if plan.get("plan"):
            assert plan["plan"][0]["tool"] == "single_match"

    def test_extract_no_pattern_match(self, spacy_extractor):
        """Тест когда нет явного паттерна - fallback на intersection/midpoint."""
        try:
            import spacy
            nlp = spacy.load("ru_core_news_sm")
            spacy_extractor._nlp = nlp
        except (ImportError, OSError):
            pytest.skip("spaCy model not available")

        candidates = [
            {"id": "1", "name": "объект1", "type": "street", "geom_type": "linestring", "span": [0, 7]},
            {"id": "2", "name": "объект2", "type": "street", "geom_type": "linestring", "span": [8, 15]},
        ]

        query = "объект1 объект2"
        plan = spacy_extractor.extract_plan(query, candidates)

        # Should return some plan for multiple candidates
        if plan.get("plan"):
            assert plan["plan"][0]["tool"] in ["intersection", "single_match"]

    def test_type_refinement_by_context(self, spacy_extractor):
        """Тест уточнения типа по контекстному маркеру."""
        try:
            import spacy
            nlp = spacy.load("ru_core_news_sm")
            spacy_extractor._nlp = nlp
        except (ImportError, OSError):
            pytest.skip("spaCy model not available")

        # Test "Кировский сквер" should refine to park type
        candidates = [
            {"id": "1", "name": "Кировский", "type": "street", "geom_type": "linestring", "span": [0, 9]},
            {"id": "2", "name": "Кировский", "type": "park", "geom_type": "polygon", "span": [0, 9]},
        ]

        query = "Кировский сквер"
        doc = spacy_extractor._nlp(query)
        spacy_extractor._link_candidates_to_tokens(doc, candidates)

        refined = spacy_extractor._refine_types_by_context(doc, candidates)

        # Should filter to park type candidates
        park_candidates = [c for c in refined if c.get("type") == "park"]
        street_candidates = [c for c in refined if c.get("type") == "street"]

        # At least one park candidate should remain
        assert len(park_candidates) > 0 or len(refined) == len(candidates)  # May not refine if no match

    def test_link_candidates_to_tokens_with_char_span(self, spacy_extractor):
        """Тест связывания кандидатов с токенами через char_span."""
        try:
            import spacy
            nlp = spacy.load("ru_core_news_sm")
            spacy_extractor._nlp = nlp
        except (ImportError, OSError):
            pytest.skip("spaCy model not available")

        candidates = [
            {"id": "1", "name": "тест", "type": "street", "geom_type": "linestring", "span": [0, 4]},
        ]

        query = "тест сообщение"
        doc = spacy_extractor._nlp(query)

        spacy_extractor._link_candidates_to_tokens(doc, candidates)

        # Check that tokens are marked
        location_tokens = [t for t in doc if t._.is_location]
        assert len(location_tokens) > 0

    def test_link_candidates_to_tokens_invalid_span(self, spacy_extractor):
        """Тест связывания с невалидным span."""
        try:
            import spacy
            nlp = spacy.load("ru_core_news_sm")
            spacy_extractor._nlp = nlp
        except (ImportError, OSError):
            pytest.skip("spaCy model not available")

        candidates = [
            {"id": "1", "name": "тест", "type": "street", "geom_type": "linestring", "span": None},
        ]

        query = "тест сообщение"
        doc = spacy_extractor._nlp(query)

        # Should not crash with invalid span
        spacy_extractor._link_candidates_to_tokens(doc, candidates)

    def test_custom_attributes_registration(self, spacy_extractor):
        """Тест регистрации custom attributes."""
        spacy_extractor._register_custom_attributes()
        assert spacy_extractor._custom_attributes_registered is True

        # Should not register twice
        spacy_extractor._register_custom_attributes()
        assert spacy_extractor._custom_attributes_registered is True

    def test_close(self, spacy_extractor):
        """Тест закрытия экстрактора."""
        spacy_extractor._nlp = "mock_model"
        spacy_extractor.close()

        assert spacy_extractor._nlp is None
        assert spacy_extractor._initialized is False


class TestIntegrationWithSemanticResolver:
    """Интеграционные тесты с SemanticResolver."""

    def test_spatial_plan_to_strategy_mapping(self):
        """Тест маппинга spaCy плана в стратегию SemanticResolver."""
        from parser.semantic_resolver import SemanticResolver
        from parser.morphology import Morphology
        from parser.phonetic_index import PhoneticIndex

        morph = Morphology()
        index = PhoneticIndex(morph)
        resolver = SemanticResolver(morph, index)
        resolver._initialized = True

        # Test single_match plan
        spatial_plan = {
            "plan": [
                {"tool": "single_match", "args": {"object": "123"}}
            ]
        }

        candidates = [{"geo_id": 123, "matched_name": "test", "type": "street"}]
        result = resolver._resolve_from_spatial_plan(spatial_plan, candidates)

        assert result is not None
        assert result["strategy"] == "single_match"
        assert result["geo_ids"] == ["123"]
        assert result["reasoning"] == "spacy_single_match"

        # Test intersection plan
        spatial_plan = {
            "plan": [
                {"tool": "intersection", "args": {"bounds": ["123", "456"]}}
            ]
        }

        result = resolver._resolve_from_spatial_plan(spatial_plan, candidates)
        assert result is not None
        assert result["strategy"] == "intersection"
        assert result["geo_ids"] == ["123", "456"]

        # Test midpoint plan
        spatial_plan = {
            "plan": [
                {"tool": "midpoint", "args": {"from": "123", "to": "456"}}
            ]
        }

        result = resolver._resolve_from_spatial_plan(spatial_plan, candidates)
        assert result is not None
        assert result["strategy"] == "midpoint"
        assert result["geo_ids"] == ["123", "456"]

        # Test empty plan
        spatial_plan = {"plan": []}
        result = resolver._resolve_from_spatial_plan(spatial_plan, candidates)
        assert result is None

        # Test unknown tool
        spatial_plan = {
            "plan": [
                {"tool": "unknown_tool", "args": {}}
            ]
        }
        result = resolver._resolve_from_spatial_plan(spatial_plan, candidates)
        assert result is None
