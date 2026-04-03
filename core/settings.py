from dataclasses import dataclass, field
from environs import Env
from typing import Optional, List, Set
import logging

logger = logging.getLogger(__name__)

# Constants for fallback (used if database is not available)
DEFAULT_STOPWORDS = (
    "в", "на", "под", "к", "с", "о", "у", "по", "за", "до", "над", "для", "от", "об", "про", "из", "же", "бы", "ни", "то", "что", "как", "если", "когда", "где", "почему", "зачем", "так", "вот", "все", "всё", "этот", "та", "то", "те", "те", "все", "всё", "всех", "всём", "всего", "всей", "всем", "всеми", "сам", "сама", "само", "сами", "самого", "самой", "самому", "самим", "самой", "самом", "самого", "самую", "самые", "самых", "самыми", "свою", "своего", "своей", "своем", "своему", "своими", "своих", "мой", "моя", "моё", "мои", "моего", "моей", "моему", "моим", "моей", "моём", "моего", "мою", "мои", "моих", "моим", "моими", "твой", "твоя", "твоё", "твои", "твоего", "твоей", "твоему", "твоим", "твоей", "твоём", "твоего", "твою", "твои", "твоих", "твоим", "твоими", "ваш", "ваша", "ваше", "ваши", "вашего", "вашей", "вашему", "вашим", "вашей", "вашем", "вашего", "вашу", "ваши", "ваших", "вашим", "вашими", "их", "него", "неё", "них", "том", "там", "тут", "тогда", "теперь", "здесь", "туда", "сюда", "повсюду", "везде", "нигде", "всюду", "везде", "никуда", "почти", "едва", "лишь", "только", "всего", "именно", "вроде", "оказывается", "кажется", "казалось", "вдруг", "опять", "уже", "еще", "ещё", "было", "были", "был", "была", "сейчас", "сегодня", "сей", "сейчасшний", "нынешний", "теперешний", "вчера", "завтра", "сейчас", "сегодня", "теперь", "тогда", "время", "пора", "ул", "улица", "пр", "проспект", "пер", "переулок", "зеленый", "оливки", "маслины", "тцк", "планшету", "планшетом", "черном", "парковке", "парковку", "парковка", "военная", "аллея", "дорога", "таврия", "выезде", "выезд", "сильпо", "атб", "долго", "доброе", "катаются", "дастер", "Рено", "бульвар", "маршрутка", "маршрутки", "светлая", "светлый", "светлая", "светлый", "повернули", "спринтер", "сообщить", "опасности", "заявка", "подписку", "ссылке", "помочь", "каналу", "видео", "щепкино"
)

DEFAULT_LAYER_KEYWORDS_COPS = (
    'коп', 'полиц', 'мусор', 'люстр', 'бп', 'блокпост', 'мигалк', 'патрул', 'б/п', 'пост'
)

DEFAULT_LAYER_KEYWORDS_BUS = (
    'бус', 'автобус', 'спринтер', 'рено', 'h1', 'h2', 'h3', 'h4', 'h5', 'фольц', 'хендай', 'Вито'
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
    entity_min_word_length: int = 4  # УВЕЛИЧЕН с 3 для фильтрации коротких слов
    entity_similarity_threshold: float = 0.65  # УВЕЛИЧЕН с 0.55 для уменьшения FP
    strict_similarity_threshold: float = 0.65  # УВЕЛИЧЕН с 0.55
    buffer_radius_m: float = 100.0
    use_word_similarity_operator: bool = True

    # LocationFinder параметры
    default_threshold: float = 0.65  # УВЕЛИЧЕН с 0.55 для уменьшения FP
    min_word_length: int = 4         # УВЕЛИЧЕН с 3 для фильтрации коротких слов
    max_ngram_length: int = 5        # Максимальная длина n-gram

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
        return ()


@dataclass
class QuestionOverlayConfig:
    """Границы зоны для событий без точной привязки к местности (круг)"""
    center_lon: float = 30.83135  # Центр по долготе
    center_lat: float = 46.49804  # Центр по широте
    radius: float = 0.045  # Радиус круга (в градусах)

    @property
    def center(self) -> tuple:
        return (self.center_lat, self.center_lon)


@dataclass
class LayerConfig:
    cops: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS_COPS)
    bus: tuple = field(default_factory=lambda: DEFAULT_LAYER_KEYWORDS_BUS)
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
                entity_similarity_threshold=env.float("ENTITY_SIMILARITY_THRESHOLD", default=0.55),
                strict_similarity_threshold=env.float("STRICT_SIMILARITY_THRESHOLD", default=0.55),
                buffer_radius_m=env.float("BUFFER_RADIUS_M", default=100.0)
            ),
            question_overlay=QuestionOverlayConfig(
                center_lon=env.float("QUESTION_OVERLAY_CENTER_LON", default=30.83135),
                center_lat=env.float("QUESTION_OVERLAY_CENTER_LAT", default=46.49804),
                radius=env.float("QUESTION_OVERLAY_RADIUS", default=0.045)
            )
        )
    except Exception as e:
        raise ValueError(f"Configuration error: {e}")


try:
    settings = load_settings(require_jwt=True)
except Exception as e:
    print(f"Failed to load settings: {e}")
    settings = None
