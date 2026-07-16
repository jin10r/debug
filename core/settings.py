from dataclasses import dataclass, field
from environs import Env
from typing import Optional
import logging
import os
import secrets

logger = logging.getLogger(__name__)

# NOTE: DEFAULT_LAYER_KEYWORDS and LAYER_PRIORITY are defined in
# parser/layer_classifier.py — the only consumer. core/settings.py
# keeps only the LayerConfig dataclass for the /api/config endpoint.


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
    host: str = "0.0.0.0"
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
    # secret автогенерируется эфемерно в памяти при старте (см. _resolve_jwt_secret),
    # если JWT_SECRET не задан в env. Это корректно, пока core — ОДИН процесс
    # (main.py: AppRunner + asyncio.run, без воркеров/форка): секрет стабилен в
    # течение жизни процесса. При масштабировании core на несколько реплик/воркеров
    # эфемерные секреты разойдутся и сломают верификацию JWT между ними — тогда
    # нужен общий секрет (задать JWT_SECRET в env или вынести в shared store).
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
    # Высокий — это ИСПРАВЛЕНИЕ опечаток (DL 1-2), а не семантический матч:
    # отсекает "среди"/"Средняя" (разные слова), пропускает "чепаевская"/
    # "чапаевская". Падежи ловит стем-индекс (Tier 1), не fuzzy.
    surface_typo_threshold: float = 0.85

    # Sliding-window: максимальный размер окна (токенов) при генерации кандидатов.
    # Окно 1..max_sliding_window охватывает улицы из 1, 2 или 3 слов.
    max_sliding_window: int = 3

    # Бонус к score для кандидатов, которым предшествует локационный предлог
    # ("на", "по", "в" и т.п.). Помогает при дедупе когда оба матча за одну улицу.
    prepositional_boost: float = 0.05

    # Мин. score вторичного матча для участия в ГЕОМЕТРИИ мультиматч-пересечений
    # (process_candidates). Ниже порога матч остаётся в matches (для прозрачности),
    # но не искажает intersection/polygon. single_match-fallback берёт лучший по score.
    geometry_min_score: float = 0.80

    # SemanticResolver — параметры интеллектуального анализатора geo-конфликтов.
    # Модель определяет стратегию (single_match/intersection/midpoint),
    # PostGIS вычисляет финальную геометрию.
    semantic_enabled: bool = True
    semantic_model: str = 'qwen2.5:0.5b'
    semantic_temperature: float = 0.0
    semantic_timeout_s: int = 10

    # Макс. дистанция (метры) для midpoint между geo-объектами.
    midpoint_max_distance_m: float = 150.0
    # Типы объектов, для которых разрешён midpoint.
    midpoint_types: tuple = ('street', 'market', 'station', 'park', 'landmark')

    # Токены-пунктуация: отфильтровываются из tokens до поиска (_strip_noise).
    punctuation_tokens: tuple = (
        '#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';',
    )

    # NOTE: Layer keyword data is defined in parser/layer_classifier.py.
    # SimilarityConfig no longer owns layer keywords — the parser reads its
    # own DEFAULT_LAYER_KEYWORDS directly.


@dataclass
class ParserConfig:
    """Параметры parser-сервиса (kurigram, photo download)."""

    # Telegram API credentials — required for pyrogram Client to connect.
    # api_id/api_hash берутся на https://my.telegram.org/apps.
    # Передаются через env (PARSER_API_ID / PARSER_API_HASH), НЕ в кодовой базе.
    api_id: Optional[int] = None
    api_hash: Optional[str] = None

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
    """Layer keywords for the /api/config endpoint.

    These defaults mirror parser/layer_classifier.py::DEFAULT_LAYER_KEYWORDS
    to keep the core's config endpoint independent of the parser module.
    """
    cops: tuple = (
        'коп', 'полиция', 'мусор', 'мусара', 'люстра', 'мигалка',
        'патруль', 'экипаж', 'мент', 'менты', 'менти', 'полицейский',
        'полицай', 'police', 'мусорня', 'мусорской',
    )
    bus: tuple = (
        'автобус', 'бус', 'хайс', 'спринтер', 'рено', 'фольксваген',
        'фольц', 'хёндай', 'Хундай', 'вито', 'сталкер', 'транспортёр',
        'h1', 'h2', 'h3', 'h4', 'h5', 'т5', 'т4', 'т3', 'т2', 'т1',
        'н1', 'н2', 'н3', 'н4', 'н5', 'буса', 'бусик', 'бусинка',
    )
    traffic: tuple = (
        'дтп', 'авария', 'пробка', 'затор', 'светофор',
        'блокпост', 'пост', 'бп', 'б/п',
    )
    pig: tuple = ()

    _ALL_LAYERS = ('bus', 'cops', 'traffic', 'pig')

    def as_dict(self) -> dict:
        """Слой → tuple ключевых слов."""
        return {layer: getattr(self, layer) for layer in self._ALL_LAYERS}


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
class LlamaConfig:
    """Локальная LLM через llama-cpp-python (замена Ollama).

    Модель Qwen2.5-0.5B Q4_K_M (~300 MB) запускается in-process —
    без внешнего сервера, без HTTP. KV-cache системного промпта
    переиспользуется между вызовами.

    Если enabled=False или модель не найдена — грациозный fallback
    к текущему пайплайну (Ollama или pre-filter rules).
    """
    enabled: bool = False
    model_path: str = '/app/models/qwen2.5-0.5b-q4_k_m.gguf'
    n_ctx: int = 2048
    batch_size: int = 8
    batch_timeout_ms: int = 50
    n_threads: int = 4
    n_gpu_layers: int = 0
    verbose: bool = False


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
    llama: LlamaConfig = field(default_factory=LlamaConfig)


def _resolve_jwt_secret(env: Env) -> str:
    """Получить секрет JWT: env-override (если задан и валиден) либо автогенерация.

    JWT_SECRET больше НЕ обязателен в env. Логика:
      - если JWT_SECRET задан в env и валиден (≥32 символов, не плейсхолдер) —
        используется как опциональный override (обратная совместимость, общий
        секрет для multi-replica деплоя);
      - иначе — генерируется эфемерный секрет в памяти (secrets.token_urlsafe).
        Стабилен в течение жизни процесса; при рестарте новый → ранее выданные
        JWT инвалидируются (см. предупреждение в JWTConfig).

    Никогда не бросает исключение — отсутствие/невалидность env-значения не
    является ошибкой, секрет просто генерируется.
    """
    # Плейсхолдеры/слабые значения из примеров — игнорируем как «не задан».
    insecure_defaults = {
        "your-secret-key",
        "your-secret-key-change-in-production",
        "your-secret-key-change-in-production-min-32-chars",
        "secret",
        "changeme",
        "change-me",
    }

    secret = env.str("JWT_SECRET", None)

    if secret:
        is_placeholder = secret.lower() in insecure_defaults or secret.startswith("your-secret")
        if len(secret) >= 32 and not is_placeholder:
            return secret  # валидный override из env
        logger.warning(
            "JWT_SECRET in env is invalid (placeholder or <32 chars) — ignoring, "
            "generating an ephemeral secret instead."
        )

    generated = secrets.token_urlsafe(48)
    logger.info(
        "JWT_SECRET not provided — generated an ephemeral per-process secret. "
        "Tokens are invalidated on restart; set JWT_SECRET in env to persist/share."
    )
    return generated


def load_settings(env_path: Optional[str] = None, require_jwt: bool = True) -> Settings:
    """Load settings — env читается ТОЛЬКО для credentials/per-deployment URL.

    Всё остальное — хардкодные дефолты в соответствующих `@dataclass`. Чтобы
    изменить калибровку матчера / параметры БД / прокси и т.п., правится
    `core/settings.py` напрямую (не env).

    Keep-list env: BOT_TOKEN, WEBAPP_URL, REDIRECT_URL. JWT_SECRET — опциональный
    override автогенерации (см. _resolve_jwt_secret), в env не обязателен.
    CHANNEL_ID захардкожен в BotConfig (не env).
    """
    env = Env()
    env.read_env(env_path)

    try:
        jwt_config = (
            JWTConfig(secret=_resolve_jwt_secret(env))
            if require_jwt else None
        )

        ollama_host = env.str("OLLAMA_HOST", None)

        # parser API credentials — handle empty-string from docker-compose ${VAR:-}
        _api_id_raw = os.environ.get('PARSER_API_ID', '').strip()
        _api_hash_raw = os.environ.get('PARSER_API_HASH', '').strip()
        api_id_val = int(_api_id_raw) if _api_id_raw else None
        api_hash_val = _api_hash_raw or None

        return Settings(
            app=AppConfig(
                telegram_validation_enabled=env.bool(
                    "TELEGRAM_VALIDATION_ENABLED", default=True
                ),
            ),
            db=DatabaseConfig(
                user=env.str("POSTGRES_USER", "postgres"),
                password=env.str("POSTGRES_PASSWORD", "postgres"),
                database=env.str("POSTGRES_DB", "postgres"),
            ),
            bot=BotConfig(
                token=env.str("BOT_TOKEN", ""),
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
                api_id=api_id_val,
                api_hash=api_hash_val,
            ),
            question_overlay=QuestionOverlayConfig(),
            ollama=OllamaConfig(
                base_url=ollama_host or 'http://host.docker.internal:11434',
            ),
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


settings = load_settings(require_jwt=True)
