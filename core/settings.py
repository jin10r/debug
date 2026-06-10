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
        'Хундай'
        'вито',
        'сталкер',
        'транспортёр',
        'h1', 'h2', 'h3', 'h4', 'h5', 'т5',
        'т5' 'т4', 'т3', 'т3', 'т2', 'т1',
         'н1', 'н2', 'н3', 'н4', 'н5'
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
    """PostgreSQL — креды захардкожены: контейнер изолирован от внешнего мира
    (нет port mapping в docker-compose), безопасно держать default `postgres`."""
    host: str = "postgres"
    port: int = 5432
    database: str = "postgres"
    user: str = "postgres"
    password: str = "postgres"
    # asyncpg pool tuning
    pool_min_size: int = 5
    pool_max_size: int = 20
    # Command timeout для одиночного SQL-запроса (process_candidates ~5-50ms,
    # default 60s достаточно при transient lag, не убивает быстрые запросы).
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
    token: str
    channel_id: str
    webapp_url: Optional[str] = None
    redirect_url: Optional[str] = None


@dataclass
class JWTConfig:
    secret: str
    access_token_ttl: int = 900  # 15 minutes
    refresh_token_ttl: int = 86400  # 24 hours
    algorithm: str = "HS256"


@dataclass
class RedisConfig:
    """Redis — внутренний кэш в изолированной docker-сети (нет ports: блока).
    Креды захардкожены: внешнего доступа нет, env-переопределение не нужно."""
    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: str = "redis"


@dataclass
class SimilarityConfig:
    """Параметры sliding-window линкера улиц и LayerClassifier.

    Используются StreetMatcher (parser/street_matcher.py) и LayerClassifier.
    """
    # Порог fuzzy-матча (0-1) для tier-3 lemma fuzzy в _link_span.
    # 0.82: отсекает ложные позитивы (0.75–0.79) при сохранении typo-матчей (≥0.83).
    entity_similarity_threshold: float = 0.82

    # Радиус псевдо-пересечений (метры) для process_candidates SQL.
    pseudo_intersection_radius_meters: float = 150.0

    # Финальный top-K результатов find_streets().
    max_entities: int = 5

    # Длиннее этого порога (символов) сообщение не считается релевантной локацией.
    max_text_length: int = 380

    # Порог fuzz.token_sort_ratio для surface fuzzy (Tier 1, 0-1).
    phonetic_match_threshold: float = 0.85
    # Включение lemma fuzzy fallback (tier-3 в _link_span).
    lemma_fallback_enabled: bool = True

    # Sliding-window: максимальный размер окна (токенов) при генерации кандидатов.
    # Окно 1..max_sliding_window охватывает улицы из 1, 2 или 3 слов.
    max_sliding_window: int = 3

    # Бонус к score для кандидатов, которым предшествует локационный предлог
    # ("на", "по", "в" и т.п.). Помогает при дедупе когда оба матча за одну улицу.
    prepositional_boost: float = 0.05

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
    history_limit: int = 60

    # Размер asyncio.Queue для входящих сообщений (производитель-потребитель).
    message_queue_maxsize: int = 100

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
class Settings:
    app: AppConfig
    db: DatabaseConfig
    bot: BotConfig
    jwt: Optional[JWTConfig] = None
    redis: RedisConfig = field(default_factory=RedisConfig)
    similarity: SimilarityConfig = field(default_factory=SimilarityConfig)
    layers: LayerConfig = field(default_factory=LayerConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    question_overlay: QuestionOverlayConfig = field(default_factory=QuestionOverlayConfig)


def _get_required_secret(env: Env) -> str:
    """
    Получить секретный ключ JWT из переменных окружения.
    
    Требует обязательной установки JWT_SECRET в production.
    Отказывается использовать значения по умолчанию из примеров.
    
    Raises:
        ValueError: Если JWT_SECRET не установлен или использует значение по умолчанию
    """
    secret = env.str("JWT_SECRET", None)
    
    if secret is None:
        raise ValueError(
            "JWT_SECRET is not set! "
            "This is a required security setting. "
            "Generate a secure key: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    
    # Проверка на значения по умолчанию из документации
    insecure_defaults = [
        "your-secret-key",
        "your-secret-key-change-in-production",
        "your-secret-key-change-in-production-min-32-chars",
        "secret",
        "changeme",
        "change-me",
    ]
    
    if secret.lower() in insecure_defaults or secret.startswith("your-secret"):
        raise ValueError(
            f"JWT_SECRET uses an insecure default value! "
            f"Current value: {secret[:20]}... "
            "Generate a secure key: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    
    # Минимальная длина 32 символа
    if len(secret) < 32:
        raise ValueError(
            f"JWT_SECRET must be at least 32 characters long (current: {len(secret)}). "
            "Generate a secure key: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    
    return secret


def _get_required_channel_id(env: Env) -> str:
    """
    Получить ID канала из переменных окружения.
    
    Требует обязательной установки CHANNEL_ID.
    Проверяет формат Telegram channel ID (должен начинаться с -100).
    
    Raises:
        ValueError: Если CHANNEL_ID не установлен или имеет неверный формат
    """
    channel_id = env.str("CHANNEL_ID", None)
    
    if channel_id is None:
        # Provide helpful error message for Docker environment
        logger.error("CHANNEL_ID is not set!")
        
        # Try to diagnose the issue by checking other env variables
        try:
            bot_token = env.str("BOT_TOKEN", "NOT_SET")
            jwt_secret = env.str("JWT_SECRET", "NOT_SET")
            
            logger.error(f"Environment diagnosis: BOT_TOKEN present = {bot_token != 'NOT_SET'}, JWT_SECRET present = {jwt_secret != 'NOT_SET'}")
        except Exception as e:
            logger.error(f"Error checking environment: {e}")
        
        raise ValueError(
            "CHANNEL_ID is not set! "
            "This is a required setting for the Telegram channel parser. "
            "Check that CHANNEL_ID is properly configured in your Docker environment. "
            "For Docker: ensure CHANNEL_ID is in docker-compose.yml app service environment variables."
        )
    
    # Проверка формата Telegram channel ID (должен начинаться с -100)
    if not channel_id.startswith("-100"):
        logger.warning(f"CHANNEL_ID has invalid format: {channel_id}")
        raise ValueError(
            f"CHANNEL_ID has invalid format! "
            f"Telegram channel IDs should start with '-100'. Current value: {channel_id}"
        )
    
    return channel_id


def load_settings(env_path: Optional[str] = None, require_jwt: bool = True) -> Settings:
    """Load settings — env читается ТОЛЬКО для credentials/per-deployment URL.

    Всё остальное — хардкодные дефолты в соответствующих `@dataclass`. Чтобы
    изменить калибровку матчера / параметры БД / прокси и т.п., правится
    `core/settings.py` напрямую (не env).

    Keep-list env: BOT_TOKEN, CHANNEL_ID, WEBAPP_URL, REDIRECT_URL, JWT_SECRET.
    """
    env = Env()
    env.read_env(env_path)

    try:
        jwt_config = (
            JWTConfig(secret=_get_required_secret(env))
            if require_jwt else None
        )

        return Settings(
            app=AppConfig(
                telegram_validation_enabled=env.bool(
                    "TELEGRAM_VALIDATION_ENABLED", default=True
                ),
            ),
            db=DatabaseConfig(),
            bot=BotConfig(
                token=env.str("BOT_TOKEN", ""),
                channel_id=_get_required_channel_id(env),
                webapp_url=env.str("WEBAPP_URL", None),
                redirect_url=env.str("REDIRECT_URL", None),
            ),
            jwt=jwt_config,
            redis=RedisConfig(),
            similarity=SimilarityConfig(),
            layers=LayerConfig(),
            parser=ParserConfig(),
            question_overlay=QuestionOverlayConfig(),
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


settings = load_settings(require_jwt=True)
