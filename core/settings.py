from dataclasses import dataclass, field
from environs import Env
from typing import Optional, List, Set
import logging

logger = logging.getLogger(__name__)

# Constants for fallback (used if database is not available)
DEFAULT_STOPWORDS = (
    "ул", "улица", "пр", "проспект", "пер", "переулок", "зеленый", "оливки", "маслины", "тцк", "планшету", "планшетом", "черном", "парковке", "парковку", "парковка", "военная", "аллея", "дорога", "таврия", "выезде", "выезд", "сильпо", "атб", "долго", "доброе", "катаются", "дастер", "Рено", "бульвар", "маршрутка", "маршрутки", "светлая", "светлый", "светлая", "светлый", "повернули", "спринтер", "сообщить", "опасности", "заявка", "подписку", "ссылке", "помочь", "каналу", "видео", "щепкино"
)

# Ключевые слова слоёв — канонические словоформы (не стемы).
# LayerClassifier лемматизирует и ключи, и токены сообщения через mawo_pymorphy3,
# поэтому все падежи/числа словоформ совпадают автоматически.
DEFAULT_LAYER_KEYWORDS_COPS = (
    'коп', 'полиция', 'мусор', 'люстра', 'мигалка', 'патруль'
)

DEFAULT_LAYER_KEYWORDS_BUS = (
    'автобус', 'бус', 'хайс', 'спринтер', 'рено', 'фольксваген', 'хёндай',
    'вито', 'сталкер', 'транспортёр', 'h1', 'h2', 'h3', 'h4', 'h5'
)

DEFAULT_LAYER_KEYWORDS_TRAFFIC = (
    'дтп', 'авария', 'пробка', 'затор', 'светофор', 'блокпост', 'пост', 'бп'
)


@dataclass
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass
class AppConfig:
    host: str
    port: int
    telegram_validation_enabled: bool = True
    
    @classmethod
    def from_env(cls, env: Env) -> 'AppConfig':
        """Создать AppConfig из переменных окружения"""
        # Читаем переменную APP_TELEGRAM_VALIDATION (из docker-compose)
        # или TELEGRAM_VALIDATION_ENABLED (напрямую из .env)
        validation_raw = env.str("APP_TELEGRAM_VALIDATION", None)
        if validation_raw is None:
            validation_raw = env.str("TELEGRAM_VALIDATION_ENABLED", "True")
        
        # Конвертируем строку в bool (поддерживаем: True/False, true/false, 1/0)
        validation_enabled = validation_raw.lower() in ("true", "1", "yes", "on")
        
        return cls(
            host=env.str("APP_HOST", "0.0.0.0"),
            port=_safe_env_int(env, "APP_PORT", 8080),
            telegram_validation_enabled=validation_enabled
        )


@dataclass
class BotConfig:
    token: str
    channel_id: str  # NEW: Add CHANNEL_ID as required field
    webapp_url: Optional[str] = None
    redirect_url: Optional[str] = None


@dataclass
class JWTConfig:
    secret: str
    access_token_ttl: int = 900  # 15 minutes
    refresh_token_ttl: int = 604800  # 7 days
    algorithm: str = "HS256"


@dataclass
class RedisConfig:
    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None


@dataclass
class SimilarityConfig:
    """Параметры калибровки лексического матчера улиц.

    Все значения настраиваются через одноимённые env-переменные (UPPER_SNAKE).
    Используются StreetMatcher (parser/street_matcher.py) и LayerClassifier
    (parser/layer_classifier.py).
    """
    stop_words: tuple = field(default_factory=lambda: DEFAULT_STOPWORDS)

    # Минимальная длина «значимого» слова в n-грамм-фильтре. Слова короче
    # отбрасываются (если только не цифры). Используется в
    # StreetMatcher._generate_ngrams.
    entity_min_word_length: int = 2

    # Порог фуззи-матча (0-1). adjusted_score = raw_score × length_bias ≥ X
    # отсекает шумовые совпадения. На 0.75 шум 0.68-0.74 не проходит.
    entity_similarity_threshold: float = 0.75

    # Радиус псевдо-пересечений (метры) для process_candidates SQL. Если два
    # alias-индекса дают разные street_id, но их геометрии не пересекаются
    # физически, ST_DWithin в этом радиусе считает их «псевдо-пересечением».
    pseudo_intersection_radius_meters: float = 150.0

    # Максимум кандидатов на один n-грам в rapidfuzz.extract(limit=N).
    # 2 — компромисс между recall и шумом: оставляет место для синонимов
    # типа «Преображенская» + «пр. Преображенский», но не плодит 3+ кандидатов
    # на одно слово («черноморка» → 3 разные улицы).
    max_candidates_per_ngram: int = 2

    # Финальный top-K результатов find_streets() возвращает в matches[].
    max_entities: int = 3

    # Length-bias коэффициенты для adjusted_score = raw × bias.
    # 1-грам ×0.85: одиночное слово менее уверенный сигнал, чем фраза.
    # 2-грам ×0.90: фраза точнее, бонус.
    length_bias_1gram: float = 0.85
    length_bias_2gram: float = 0.90

    # Длиннее этого порога (символов) сообщение НЕ считается релевантной
    # локацией: поиск улиц пропускается, событию назначается random точка.
    max_text_length: int = 380

    # ─── SymSpell pre-correction опечаток (parser/typo_corrector.py) ──────
    # Включение pre-correction. False = быстрый rollback без перезапуска.
    typo_correction_enabled: bool = True
    # Edit distance Левенштейна: 1 = одна опечатка, 2 = до двух (рекомендуется).
    typo_correction_max_edit_distance: int = 2
    # Минимальная длина слова для коррекции. Короткие слова (≤3) дают много
    # ложных match'ей (например «улу» → «ул»), поэтому фильтруются.
    typo_correction_min_word_length: int = 4

    def get_stopwords(self) -> Set[str]:
        """Стоп-слова (используются в _generate_ngrams)."""
        return set(DEFAULT_STOPWORDS)

    def get_layer_keywords(self, layer: str) -> tuple:
        """Ключевые слова слоя для LayerClassifier."""
        if layer == 'cops':
            return DEFAULT_LAYER_KEYWORDS_COPS
        elif layer == 'bus':
            return DEFAULT_LAYER_KEYWORDS_BUS
        elif layer == 'traffic':
            return DEFAULT_LAYER_KEYWORDS_TRAFFIC
        return ()


@dataclass
class ParserConfig:
    """Параметры parser-сервиса (monitoring.py)."""

    # Сколько сообщений тянуть из истории канала при старте парсера.
    # Высокое значение увеличивает startup latency, низкое — пропускает старые.
    backfill_limit: int = 25

    # Размер asyncio.Queue для входящих сообщений (производитель-потребитель).
    message_queue_maxsize: int = 1000


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
    cops: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS_COPS)
    bus: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS_BUS)
    traffic: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS_TRAFFIC)
    pig: tuple = field(default=())


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


def _safe_env_float(env: Env, key: str, default: float) -> float:
    """env.float() с дополнительной защитой: пустая строка → default.

    `environs.Env.float(KEY, default=X)` рассматривает `KEY=` (empty) как
    «переменная задана» и поднимает EnvValidationError. Этот хелпер трактует
    empty/whitespace как «не задана» → fallback на default. Невалидное
    числовое значение тоже даёт default + warning.
    """
    raw = env.str(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        logger.warning(f"Invalid float for {key}={raw!r}, using default {default}")
        return default


def _safe_env_int(env: Env, key: str, default: int) -> int:
    """То же что `_safe_env_float`, но для int."""
    raw = env.str(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning(f"Invalid int for {key}={raw!r}, using default {default}")
        return default


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
    """Loads settings from environment variables."""
    env = Env()
    env.read_env(env_path)

    try:
        jwt_config = None
        if require_jwt:
            jwt_config = JWTConfig(
                secret=_get_required_secret(env),
                access_token_ttl=_safe_env_int(env, "JWT_ACCESS_TTL", 900),
                refresh_token_ttl=_safe_env_int(env, "JWT_REFRESH_TTL", 604800),
            )
        
        return Settings(
            app=AppConfig.from_env(env),
            db=DatabaseConfig(
                host=env.str("DB_HOST", "postgres"),
                port=_safe_env_int(env, "DB_PORT", 5432),
                database=env.str("DB_NAME", "map"),
                user=env.str("DB_USER", "postgres"),
                password=env.str("DB_PASSWORD", "postgres"),
            ),
            bot=BotConfig(
                token=env.str("BOT_TOKEN", ""),
                channel_id=_get_required_channel_id(env),  # NEW: Add validated channel_id
                webapp_url=env.str("WEBAPP_URL", None),
                redirect_url=env.str("REDIRECT_URL", None)
            ),
            jwt=jwt_config,
            redis=RedisConfig(
                host=env.str("REDIS_HOST", "redis"),
                port=_safe_env_int(env, "REDIS_PORT", 6379),
                db=_safe_env_int(env, "REDIS_DB", 0),
                password=env.str("REDIS_PASSWORD", None),
            ),
            similarity=SimilarityConfig(
                entity_min_word_length=_safe_env_int(env, "ENTITY_MIN_WORD_LENGTH", 2),
                entity_similarity_threshold=_safe_env_float(env, "ENTITY_SIMILARITY_THRESHOLD", 0.75),
                pseudo_intersection_radius_meters=_safe_env_float(
                    env, "PSEUDO_INTERSECTION_RADIUS_METERS", 150.0
                ),
                max_candidates_per_ngram=_safe_env_int(env, "MAX_CANDIDATES_PER_NGRAM", 2),
                max_entities=_safe_env_int(env, "MAX_ENTITIES", 3),
                length_bias_1gram=_safe_env_float(env, "LENGTH_BIAS_1GRAM", 0.85),
                length_bias_2gram=_safe_env_float(env, "LENGTH_BIAS_2GRAM", 0.90),
                max_text_length=_safe_env_int(env, "MAX_TEXT_LENGTH", 380),
                typo_correction_enabled=env.bool("TYPO_CORRECTION_ENABLED", default=True),
                typo_correction_max_edit_distance=_safe_env_int(
                    env, "TYPO_CORRECTION_MAX_EDIT_DISTANCE", 2
                ),
                typo_correction_min_word_length=_safe_env_int(
                    env, "TYPO_CORRECTION_MIN_WORD_LENGTH", 4
                ),
            ),
            parser=ParserConfig(
                backfill_limit=_safe_env_int(env, "BACKFILL_LIMIT", 25),
                message_queue_maxsize=_safe_env_int(env, "MESSAGE_QUEUE_MAXSIZE", 1000),
            ),
            question_overlay=QuestionOverlayConfig(
                center_lon=_safe_env_float(env, "QUESTION_OVERLAY_CENTER_LON", 30.83135),
                center_lat=_safe_env_float(env, "QUESTION_OVERLAY_CENTER_LAT", 46.49804),
                radius=_safe_env_float(env, "QUESTION_OVERLAY_RADIUS", 0.045),
            )
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


settings = load_settings(require_jwt=True)
