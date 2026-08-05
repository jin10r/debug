"""Тесты SemanticMatcher (rubert-tiny2 ONNX) без тяжёлых runtime-зависимостей.

ONNX-модель и токенизатор не загружаются: тесты подменяют _embed_single и
предрасчитанные эмбеддинги стабами (numpy). Проверяются три ключевых свойства,
которые ломались в проде:

  • фильтр серой зоны (0.70–0.85) принимает/отклоняет по ПОЛНОМУ тексту;
  • _embed_single возвращает 1D-вектор (hidden,) — баг с 2D ломал float(np.dot);
  • fallback эмбеддинга по geo_id выбирает семантически ближайший алиас.
"""

import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent

if "processor" not in sys.modules:
    _pkg = types.ModuleType("processor")
    _pkg.__path__ = [str(ROOT / "processor")]
    sys.modules["processor"] = _pkg

# semantic_matcher.py импортирует onnxruntime/tokenizers/prometheus_client на
# уровне модуля. В лёгком dev-окружении их может не быть — стабим (тесты не
# загружают ONNX-модель и не трогают метрики).
import importlib.util  # noqa: E402


def _missing(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return True


if _missing("onnxruntime"):
    _m = types.ModuleType("onnxruntime")
    _m.InferenceSession = object
    sys.modules["onnxruntime"] = _m

if _missing("tokenizers"):
    _m = types.ModuleType("tokenizers")
    _m.Tokenizer = object
    sys.modules["tokenizers"] = _m

if _missing("prometheus_client"):
    _m = types.ModuleType("prometheus_client")

    class _Counter:
        def __init__(self, *a, **k):
            pass

        def inc(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

    _m.Counter = _Counter
    _m.generate_latest = lambda *a, **k: b""
    _m.CONTENT_TYPE_LATEST = "text/plain"
    _m.REGISTRY = object()
    sys.modules["prometheus_client"] = _m

from processor.semantic_matcher import (  # noqa: E402
    SemanticMatcher,
    FUZZY_CONFIDENT_THRESHOLD,
    FUZZY_GRAY_ZONE_LOW,
)


def _make_matcher(aliases, query_emb):
    """Матрица с готовыми эмбеддингами без ONNX-инициализации.

    Векторы нормализуются до единичной длины, чтобы косинусная близость
    совпадала с dot-произведением (как в проде после normalize).
    """
    m = SemanticMatcher.__new__(SemanticMatcher)
    norm = lambda v: v / max(float(np.linalg.norm(v)), 1e-9)  # noqa: E731
    q = norm(np.asarray(query_emb, dtype=np.float64))
    m._geo_embeddings = np.array([q, q], dtype=np.float64)
    m._geo_meta = [
        {"geo_id": 1, "name": aliases[0], "alias_idx": 0},
        {"geo_id": 2, "name": aliases[1], "alias_idx": 0},
    ]
    m._alias_index = {a.lower(): i for i, a in enumerate(aliases)}
    # Стаб эмбеддера текста: без ONNX-сессии возвращает фиксированный вектор.
    m._embed_single = lambda text: q
    return m


# ------------------------------------------------------------ 1D shape bug

def test_embed_single_shape():
    """_embed_single должен возвращать 1D (hidden,) — регрессия 2D-бага.

    Баг: outputs[0][0, :] возвращал (seq_len, hidden)=2D → np.dot давал
    массив (1,) → float() падал в numpy 2.x. Теперь [0, 0, :] — [CLS] 1D.
    """
    m = SemanticMatcher.__new__(SemanticMatcher)
    calls = {}

    def fake_session_run(outputs, feed):
        calls["feed"] = feed
        # (batch=1, seq_len=64, hidden=3)
        arr = np.zeros((1, 64, 3), dtype=np.float32)
        arr[0, 0, :] = [1.0, 2.0, 3.0]
        return [arr]

    m._session = types.SimpleNamespace(run=fake_session_run)
    m._tokenizer = types.SimpleNamespace(
        encode=lambda t: types.SimpleNamespace(
            ids=[1, 2, 3], attention_mask=[1, 1, 1]
        )
    )
    emb = m._embed_single("тест")
    assert emb.ndim == 1, f"expected 1D, got {emb.ndim}D"
    assert emb.shape == (3,)
    # нормализован
    assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-6


# -------------------------------------------------------- gray-zone accept/reject

def test_gray_zone_accept():
    """Кандидат серой зоны, реально упомянутый в тексте → принимается."""
    m = _make_matcher(["Балковская", "Красивая"], np.array([0.5, 0.5]))
    cand = {"geo_id": 1, "score": 0.82, "matched_name": "Балковская", "source": "surface_typo"}
    out = m.filter_candidates([dict(cand)], "на балковская возле магазина стоят два буса")
    assert len(out) == 1
    assert out[0]["geo_id"] == 1
    assert "semantic_score" in out[0]
    assert "+semantic" in out[0]["source"]


def test_gray_zone_reject():
    """Кандидат серой зоны без реального упоминания → отклоняется."""
    m = _make_matcher(["Балковская", "Красивая"], np.array([0.5, 0.5]))
    # Эмбеддер текста вернёт вектор, ортогональный алиасу (dot=0 < 0.55).
    m._embed_single = lambda text: np.array([0.5, -0.5], dtype=np.float64)
    cand = {"geo_id": 1, "score": 0.82, "matched_name": "Балковская", "source": "surface_typo"}
    out = m.filter_candidates([dict(cand)], "сегодня отличная погода, планируем прогулку")
    assert out == []


def test_confident_candidate_untouched():
    """Кандидаты ≥ confident-порога (в т.ч. стем-exact 0.97) модель не трогает."""
    m = _make_matcher(["Балковская", "Красивая"], np.array([0.5, 0.5]))
    cand = {"geo_id": 1, "score": 0.97, "matched_name": "Балковская", "source": "stem_exact"}
    out = m.filter_candidates([dict(cand)], "совершенно другой текст без улиц")
    assert len(out) == 1
    assert out[0]["source"] == "stem_exact"  # без "+semantic"


# --------------------------------------------------------- alias embedding fallback

def test_fallback_picks_best_alias():
    """Fallback эмбеддинга по geo_id берёт семантически ближайший алиас."""
    # query ближе к алиасу 1 (dot 0.9), чем к алиасу 2 (dot 0.1)
    query_emb = np.array([1.0, 0.0])
    m = _make_matcher(["Молдаванка", "Балковская"], query_emb)
    m._geo_embeddings = np.array([[1.0, 0.0], [0.0, 1.0]])
    cand = {"geo_id": 1, "score": 0.82, "matched_name": "НетТакогоАлиаса", "source": "surface_typo"}
    emb = m._find_alias_embedding(cand, query_emb)
    assert emb is not None
    assert float(np.dot(query_emb, emb)) > 0.9  # взяли ближайший алиас того же geo_id


def test_missing_embedding_keeps_candidate():
    """Нет эмбеддинга → кандидат НЕ дропается молча (сохраняется консервативно)."""
    m = _make_matcher(["Балковская", "Красивая"], np.array([0.5, 0.5]))
    m._alias_index = {}  # выключим индекс — fallback тоже пустой
    m._geo_meta = []
    cand = {"geo_id": 999, "score": 0.82, "matched_name": "Призрак", "source": "surface_typo"}
    out = m.filter_candidates([dict(cand)], "любой текст")
    assert len(out) == 1
    assert out[0]["geo_id"] == 999


# -------------------------------------------------------------- threshold sanity

def test_threshold_relationship():
    """Серая зона [0.70, 0.85) достижима из Tier 2 (surface_typo ≥ 0.80)."""
    assert FUZZY_GRAY_ZONE_LOW <= 0.80  # Tier 2 порог в settings
    assert FUZZY_CONFIDENT_THRESHOLD > 0.80  # уверенность выше серой зоны
