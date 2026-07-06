from dataclasses import dataclass, field
from environs import Env
from typing import Optional
import logging
import secrets

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
    surface_typo_threshold: float = 0.90

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

    def get_layer_keywords(self, layer: str) -> tuple:
        return DEFAULT_LAYER_KEYWORDS.get(layer, ())


@dataclass
class ParserConfig:
    """Параметры parser-сервиса (monitoring.py)."""

    # Сколько сообщений тянуть из истории канала при старте парсера.
    # Высокое значение увеличивает startup latency, низкое — пропускает старые.
    # Это же значение — механизм recovery после краша: бэкфилл переобрабатывает
    # последние history_limit сообщений (дедуп ON CONFLICT делает это безопасным),
    # поэтому оно должно с запасом перекрывать окно простоя × rate сообщений.
    history_limit: int = 100

    # Размер asyncio.Queue для входящих сообщений (производитель-потребитель).
    # Запас под всплески (бэкфилл больших историй + live-пики); put() блокирует
    # при заполнении (backpressure, без потерь), пул воркеров сливает очередь.
    message_queue_maxsize: int = 100

    # Число конкурентных воркеров очереди. Перекрывает CPU одного сообщения с
    # DB-I/O другого на всплесках. Безопасно: вставки идемпотентны (ON CONFLICT),
    # порядок не важен, asyncpg-pool конкурентно-безопасен, в CPU-секциях нет
    # await. Кап ≤8 в monitoring.py, чтобы не исчерпать pool (pool_max_size=20).
    worker_concurrency: int = 5

    # Каталог хранения медиафайлов (фотографии событий). Монтируется через
    # volume в docker-compose, путь синхронизирован с разделом volumes.
    events_media_dir: str = "/media/events"

    # SOCKS5/HTTP proxy для pyrogram (если телеграм блокируется в сети
    # развёртывания). None = без proxy. Меняется правкой settings.py для
    # конкретной инсталляции — не env.
    socks5_host: Optional[str] = None
    proxy_host: Optional[str] = None
    proxy_scheme: str = "socks5"
    proxy_port: int = 1080


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
class SpaCyConfig:
    """SpaCy semantic spatial relation extraction.

    Опциональный модуль для уточнения типов объектов и извлечения пространственных
    отношений на основе синтаксического анализа. Может быть отключён для
    экономии ресурсов (enabled=False).
    """
    enabled: bool = True
    model_name: str = 'ru_core_news_sm'
    timeout_ms: int = 50


@dataclass
class Settings:
    app: AppConfig
    db: DatabaseConfig
    bot: BotConfig
    jwt: Optional[JWTConfig] = None
    similarity: SimilarityConfig = field(default_factory=SimilarityConfig)
    layers: LayerConfig = field(default_factory=LayerConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    question_overlay: QuestionOverlayConfig = field(default_factory=QuestionOverlayConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    spacy: SpaCyConfig = field(default_factory=SpaCyConfig)


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

        return Settings(
            app=AppConfig(
                telegram_validation_enabled=env.bool(
                    "TELEGRAM_VALIDATION_ENABLED", default=True
                ),
            ),
            db=DatabaseConfig(),
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
            ),
            question_overlay=QuestionOverlayConfig(),
            ollama=OllamaConfig(
                base_url=ollama_host or 'http://host.docker.internal:11434',
            ),
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


settings = load_settings(require_jwt=True)
