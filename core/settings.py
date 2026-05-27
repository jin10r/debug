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
    'коп', 'полиция', 'мусор', 'люстра', 'блокпост', 'мигалка', 'патруль', 'пост', 'бп'
)

DEFAULT_LAYER_KEYWORDS_BUS = (
    'автобус', 'бус', 'хайс', 'спринтер', 'рено', 'фольксваген', 'хёндай',
    'вито', 'сталкер', 'транспортёр', 'h1', 'h2', 'h3', 'h4', 'h5'
)

DEFAULT_LAYER_KEYWORDS_TRAFFIC = (
    'дтп', 'авария', 'пробка', 'затор', 'светофор'
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
            port=env.int("APP_PORT", 8080),
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
    stop_words: tuple = field(default_factory=lambda: DEFAULT_STOPWORDS)
    entity_min_word_length: int = 3  # Учитываем все слова от 3 символов
    entity_similarity_threshold: float = 0.67  # Порог фуззи-матча (0-1)
    # Радиус псевдо-пересечений (метры) для process_candidates SQL. Если два
    # alias-индекса дают разные street_id, но их геометрии не пересекаются
    # физически, ST_DWithin в этом радиусе считает их «псевдо-пересечением».
    pseudo_intersection_radius_meters: float = 150.0

    # Поля для загрузки из БД
    db_stopwords: Set[str] = field(default_factory=set)
    db_layer_keywords: dict = field(default_factory=dict)

    def get_stopwords(self) -> Set[str]:
        """Вернуть стоп-слова (из БД или fallback)."""
        return self.db_stopwords if self.db_stopwords else set(DEFAULT_STOPWORDS)

    def get_layer_keywords(self, layer: str) -> tuple:
        """Вернуть ключевые слова для слоя (из БД или fallback)."""
        if layer in self.db_layer_keywords and self.db_layer_keywords[layer]:
            return tuple(self.db_layer_keywords[layer])

        # Fallback
        if layer == 'cops':
            return DEFAULT_LAYER_KEYWORDS_COPS
        elif layer == 'bus':
            return DEFAULT_LAYER_KEYWORDS_BUS
        elif layer == 'traffic':
            return DEFAULT_LAYER_KEYWORDS_TRAFFIC
        return ()


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
    """Loads settings from environment variables."""
    env = Env()
    env.read_env(env_path)

    try:
        jwt_config = None
        if require_jwt:
            jwt_config = JWTConfig(
                secret=_get_required_secret(env),
                access_token_ttl=env.int("JWT_ACCESS_TTL", default=900),
                refresh_token_ttl=env.int("JWT_REFRESH_TTL", default=604800)
            )
        
        return Settings(
            app=AppConfig.from_env(env),
            db=DatabaseConfig(
                host=env.str("DB_HOST", "postgres"),
                port=env.int("DB_PORT", 5432),
                database=env.str("DB_NAME", "map"),
                user=env.str("DB_USER", "postgres"),
                password=env.str("DB_PASSWORD", "postgres")
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
                port=env.int("REDIS_PORT", 6379),
                db=env.int("REDIS_DB", 0),
                password=env.str("REDIS_PASSWORD", None)
            ),
            similarity=SimilarityConfig(
                entity_min_word_length=env.int("ENTITY_MIN_WORD_LENGTH", default=3),
                entity_similarity_threshold=env.float("ENTITY_SIMILARITY_THRESHOLD", default=0.67),
                pseudo_intersection_radius_meters=env.float(
                    "PSEUDO_INTERSECTION_RADIUS_METERS", default=150.0
                ),
            ),
            question_overlay=QuestionOverlayConfig(
                center_lon=env.float("QUESTION_OVERLAY_CENTER_LON", default=30.83135),
                center_lat=env.float("QUESTION_OVERLAY_CENTER_LAT", default=46.49804),
                radius=env.float("QUESTION_OVERLAY_RADIUS", default=0.045)
            )
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


settings = load_settings(require_jwt=True)
