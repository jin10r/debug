from dataclasses import dataclass, field
from environs import Env
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Ключевые слова слоёв — канонические словоформы (не стемы).
# LayerClassifier лемматизирует и ключи, и токены сообщения через mawo_pymorphy3,
# поэтому все падежи/числа словоформ совпадают автоматически.
#
# Порядок ключей задаёт приоритет классификации: первый совпавший слой
# выигрывает (см. parser/layer_classifier.py). 'pig' — fallback без ключей.
DEFAULT_LAYER_KEYWORDS: dict[str, tuple] = {
    'bus': (
        'автобус',
        'бус',
        'хайс',
        'спринтер',
        'рено',
        'фольксваген',
        'хёндай',
        'Хундай',
        'вито',
        'вольксваген',
        'Кадди',
        'сталкер',
        'транспортёр',
        'h1', 'h2', 'h3', 'h4', 'h5',
        'т5', 'т4', 'т3', 'т2', 'т1',
        'н1', 'н2', 'н3', 'н4', 'н5',
        # pymorphy лемматизирует «бус»→«бусы», но «буса»/«бусик»→самостоятельные
        # леммы ⇒ косвенные/слэнговые формы не совпадали. Добавлены явно.
        'буса', 'бусик',
    ),
    'cops': (
        'коп',
        'полиция',
        'мусор',
        'мусара',
        'люстра',
        'мигалка',
        'патруль',
        'экипаж',
        'мент',
        'менты',
        'полицейский',
        'полицай',
        'police',
        'мусорня',
        'мусорской'
        
    ),
    'traffic': (
        'дтп',
        'авария',
        'пробка',
        'затор',
        'светофор',
        'блокпост',
        'пост',
        'бп',
        'б/п'
    ),
    'pig': (),
}

# Порядок приоритета (исключая fallback 'pig').
LAYER_PRIORITY: tuple = tuple(k for k in DEFAULT_LAYER_KEYWORDS if k != 'pig')


@dataclass
class DatabaseConfig:
    """PostgreSQL — прямое подключение (без PgBouncer)."""
    host: str = "postgres"
    port: int = 5432
    database: str = "postgres"
    user: str = "postgres"
    password: str = "postgres"
    # Прямое подключение: каждый коннект = backend process в postgres.
    # pool_max_size=30 — оптимизировано для 1GB postgres контейнера.
    pool_min_size: int = 5
    pool_max_size: int = 30
    # Command timeout для SQL-запроса. Role parser имеет 60s timeout
    # в postgresql.conf, core — 30s.
    command_timeout: int = 60


@dataclass
class AppConfig:
    host: str = "0.0.0.0"  # nosec B104 — bind all interfaces (nginx reverse proxy)
    port: int = 8080
    telegram_validation_enabled: bool = True
    # Логирование (main.py, parser/monitoring.py читают эти поля)
    log_level: str = "INFO"
    log_format: str = "json"  # json | text
    # CORS: пустой кортеж = same-origin only (nginx проксирует фронтенд →
    # CORS не нужен). При явном списке доменов app_factory включает CORS.
    allowed_origins: tuple = ()


@dataclass
class BotConfig:
    # channel_id захардкожен (как DB-параметры): per-deployment
    # идентификатор канала мониторинга меняется правкой settings.py, не env.
    token: str
    channel_id: str = "-1002050105527"
    webapp_url: Optional[str] = None
    redirect_url: Optional[str] = None


@dataclass
class JWTConfig:
    secret: str
    access_token_ttl: int = 900  # 15 minutes
    refresh_token_ttl: int = 86400  # 24 hours
    algorithm: str = "HS256"


@dataclass
class SimilarityConfig:
    """Параметры sliding-window линкера гео-объектов и LayerClassifier.

    Используются GeoMatcher (parser/geo_matcher.py) и LayerClassifier.
    """
    # Порог fuzzy-матча (0-1) для tier-3 lemma fuzzy в _link_span.
    # 0.82: отсекает ложные позитивы (0.75–0.79) при сохранении typo-матчей (≥0.83).
    entity_similarity_threshold: float = 0.82

    # Радиус для midpoint (метры) — макс. дистанция между геометриями.
    pseudo_intersection_radius_meters: float = 150.0

    # Финальный top-K результатов find_geo().
    max_entities: int = 5

    # Длиннее этого порога (символов) сообщение не считается релевантной локацией.
    max_text_length: int = 380

    # Порог fuzz.token_sort_ratio для surface fuzzy (Tier 1, 0-1).
    phonetic_match_threshold: float = 0.85
    # Включение lemma fuzzy fallback (tier-3 в _link_span).
    lemma_fallback_enabled: bool = True

    # Порог fuzz.ratio для surface-орфо-корректора (Tier 2 в _link_span, 0-1).
    # 0.80: пропускает в «серую зону» (0.80–0.85) слабее совпадения, которые
    # ранее валидировала семантическая модель (см. semantic_accept_threshold).
    # Точные стем-матчи (Tier 1, score varies) модель не затрагивает.
    surface_typo_threshold: float = 0.80

    # Включение SemanticMatcher (ONNX rubert-tiny2). По умолчанию ОТКЛЮЧЕН:
    # проверка живых данных показала semantic_checked_total=0 — серая зона
    # (0.70–0.85) пуста, все реальные кандидаты ≥0.85 проходят как confident.
    # Модель тратила +20 сек старта и 116 МБ без единого решения.
    # См. docs/GEOMETRY_ANALYSIS.md §12–13. Включить для будущего re-enable:
    # обучать голову на эмбеддингах модели (см. §12.4) или расширить серую зону.
    semantic_enabled: bool = False

    # Порог (0-1) косинусной близости ПОЛНОГО текста сообщения к названию
    # geo-объекта для приёма кандидата из серой зоны (SemanticMatcher).
    # 0.55: пропускает реальные упоминания улицы в контексте, отсекает
    # случайные совпадения поверхностей. Только для кандидатов 0.70–0.85.
    # Неактуален при semantic_enabled=False.
    semantic_accept_threshold: float = 0.55

    # Sliding-window: максимальный размер окна (токенов) при генерации кандидатов.
    # Окно 1..max_sliding_window охватывает улицы из 1, 2 или 3 слов.
    max_sliding_window: int = 3

    # Бонус к score для кандидатов, которым предшествует локационный предлог
    # ("на", "по", "в" и т.п.). Помогает при дедупе когда оба матча за одну улицу.
    prepositional_boost: float = 0.05

    # Мин. score вторичного матча для участия в ГЕОМЕТРИИ мультиматч-пересечений
    # (process_candidates). Ниже порога матч остаётся в matches (для прозрачности),
    # но не искажает intersection/polygon. single_match-fallback берёт лучший по score.
    geometry_min_score: float = 0.85

    # Макс. дистанция (метры) для midpoint между geo-объектами.
    midpoint_max_distance_m: float = 150.0
    # Типы объектов, для которых разрешён midpoint.
    midpoint_types: tuple = ('street', 'market', 'station', 'park', 'landmark')

    # Токены-пунктуация: отфильтровываются из tokens до поиска (_strip_noise).
    punctuation_tokens: tuple = (
        '#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';',
    )

    def get_layer_keywords(self, layer: str) -> tuple:
        return DEFAULT_LAYER_KEYWORDS.get(layer, ())


@dataclass
class ParserConfig:
    """Параметры parser-сервиса (kurigram, photo download)."""

    # Сколько сообщений тянуть из истории канала при старте парсера.
    history_limit: int = 100

    # Каталог хранения медиафайлов (фотографии событий).
    events_media_dir: str = "/media/events"

    # SOCKS5/HTTP proxy для pyrogram.
    socks5_host: Optional[str] = None
    proxy_host: Optional[str] = None
    proxy_scheme: str = "socks5"
    proxy_port: int = 1080


@dataclass
class ProcessorConfig:
    """Параметры processor-сервиса (NLP pipeline)."""

    # Число конкурентных воркеров, потребляющих из pending_events (SKIP LOCKED).
    worker_concurrency: int = 5

    # Polling interval (сек) при пустой очереди.
    poll_interval: float = 0.5


@dataclass
class QuestionOverlayConfig:
    """Границы зоны для событий без точной привязки к местности (круг)"""
    center_lon: float = 30.83135  # Центр по долготе
    center_lat: float = 46.49804  # Центр по широте
    radius: float = 0.04  # Радиус круга (в градусах)

    @property
    def center(self) -> tuple:
        return (self.center_lat, self.center_lon)


@dataclass
class LayerConfig:
    cops: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS['cops'])
    bus: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS['bus'])
    traffic: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS['traffic'])
    pig: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS['pig'])

    def as_dict(self) -> dict:
        """Слой → tuple ключевых слов. Порядок соответствует LAYER_PRIORITY + 'pig'."""
        return {layer: getattr(self, layer) for layer in DEFAULT_LAYER_KEYWORDS}


@dataclass
class OllamaConfig:
    """Ollama LLM geo-resolution (Tier-2 fallback).

    Всегда активен (enabled=True), но НЕ обязателен — при недоступности хоста
    приложение продолжает работу без ошибок. Хост переопределяется через env
    OLLAMA_HOST (по умолчанию http://host.docker.internal:11434).
    """
    enabled: bool = False
    base_url: str = 'http://host.docker.internal:11434'
    model: str = 'qwen2.5:0.5b'
    timeout_s: int = 15
    max_tokens: int = 128


@dataclass
class Settings:
    app: AppConfig
    db: DatabaseConfig
    bot: BotConfig
    jwt: Optional[JWTConfig] = None
    similarity: SimilarityConfig = field(default_factory=SimilarityConfig)
    layers: LayerConfig = field(default_factory=LayerConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    processor: ProcessorConfig = field(default_factory=ProcessorConfig)
    question_overlay: QuestionOverlayConfig = field(default_factory=QuestionOverlayConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)


def _resolve_jwt_secret(env: Env) -> str:
    secret = env.str("JWT_SECRET", None)
    if not secret:
        raise RuntimeError("FATAL: JWT_SECRET is required in environment (R-C8).")
    insecure_defaults = {
        "your-secret-key",
        "your-secret-key-change-in-production",
        "your-secret-key-change-in-production-min-32-chars",
        "secret",
        "changeme",
        "change-me",
    }
    if secret.lower() in insecure_defaults or secret.startswith("your-secret"):
        raise RuntimeError(
            "FATAL: JWT_SECRET is a placeholder — set a real secret (R-C8)."
        )
    if len(secret) < 32:
        raise RuntimeError(
            f"FATAL: JWT_SECRET must be >= 32 chars (got {len(secret)}) (R-C8)."
        )
    return secret


def load_settings(env_path: Optional[str] = None, require_jwt: bool = True) -> Settings:
    """Load settings — env читается ТОЛЬКО для credentials/per-deployment URL.

    Всё остальное — хардкодные дефолты в соответствующих `@dataclass`. Чтобы
    изменить калибровку матчера / параметры БД / прокси и т.п., правится
    `core/settings.py` напрямую (не env).

    Keep-list env: BOT_TOKEN, WEBAPP_URL, REDIRECT_URL. JWT_SECRET — обязателен
    при require_jwt=True (R-C8).
    CHANNEL_ID захардкожен в BotConfig (не env).
    """
    env = Env()
    env.read_env(env_path)

    try:
        telegram_validation_enabled = env.bool(
            "TELEGRAM_VALIDATION_ENABLED", default=True
        )
        bot_token = env.str("BOT_TOKEN", "")
        try:
            jwt_secret = _resolve_jwt_secret(env)
            jwt_config = JWTConfig(secret=jwt_secret)
        except RuntimeError:
            if require_jwt:
                raise
            jwt_config = None

        ollama_host = env.str("OLLAMA_HOST", None)

        return Settings(
            app=AppConfig(
                telegram_validation_enabled=telegram_validation_enabled,
            ),
            db=DatabaseConfig(
                user=env.str("POSTGRES_USER", "postgres"),
                password=env.str("POSTGRES_PASSWORD", "postgres"),
                database=env.str("POSTGRES_DB", "postgres"),
            ),
            bot=BotConfig(
                token=bot_token,
                webapp_url=env.str("WEBAPP_URL", None),
                redirect_url=env.str("REDIRECT_URL", None),
            ),
            jwt=jwt_config,
            similarity=SimilarityConfig(),
            layers=LayerConfig(),
            parser=ParserConfig(
                socks5_host=env.str("PROXY_HOST", None),
                proxy_port=env.int("PROXY_PORT", 1080),
                proxy_scheme=env.str("PROXY_SCHEME", "socks5"),
            ),
            question_overlay=QuestionOverlayConfig(),
            ollama=OllamaConfig(
                base_url=ollama_host or 'http://host.docker.internal:11434',
            ),
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


settings = load_settings(require_jwt=False)
