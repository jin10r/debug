from dataclasses import dataclass, field
from environs import Env
from typing import Optional, List, Set
import logging

logger = logging.getLogger(__name__)

# Constants for fallback (used if database is not available)
DEFAULT_STOPWORDS = (
    # Уличные постфиксы / типы объектов (как часть n-gram, но не самостоятельно)
    "ул", "улица", "пр", "проспект", "пер", "переулок", "бульвар", "аллея",
    "дорога", "выезде", "выезд", "парк",
    # Локативные предлоги/наречия (R3, основной источник substring-FP)
    "сторона", "сторону", "стороны",
    "возле", "около", "рядом", "перед", "после", "между", "напротив",
    "район", "середине", "посередине",
    # Количественные/числительные именованные (R3)
    "много", "мало", "пара", "двое", "трое", "четверо", "несколько", "оба",
    # Единицы измерения (R3 — «Метров» ≠ «Метро» по требованию пользователя)
    "метр", "метров", "метра", "метре",
    # Бытовые/общеупотребительные существительные (R3)
    "дом", "дома", "дому", "доме", "роддом",
    "двор", "дворе", "дворы", "дворов", "дворах",
    "город", "города", "городе", "посёлок",
    "арка", "аркой", "арки", "арке",
    "стоп", "стопа",
    "отделения", "отделение",
    "почты", "почта",
    # Цвета (для машин/одежды — не топонимы)
    "черный", "чёрный", "черном", "чёрном", "белый", "белом", "серый", "серым",
    "зеленый", "зелёный", "красный", "красном", "синий", "светлая", "светлый",
    # Часто употребляемые контекстные слова из одесского корпуса
    "оливки", "оливок", "маслины", "тцк", "планшету", "планшетом",
    "парковке", "парковку", "парковка", "военная", "таврия", "сильпо", "атб",
    "долго", "доброе", "катаются", "дастер", "Рено", "маршрутка", "маршрутки",
    "повернули", "спринтер", "сообщить", "опасности", "заявка", "подписку",
    "ссылке", "помочь", "каналу", "видео", "щепкино",
    "менты", "мент", "мента", "мусора", "люди", "ребята", "ребят",
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
    refresh_token_ttl: int = 604800  # 7 days
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
    # отсекает шумовые совпадения. Для 2+ gram держим базовый 0.75.
    entity_similarity_threshold: float = 0.75
    # R4: отдельный (более жёсткий) порог для 1-gram T3 tier-B fuzzy matches.
    # Borderline 0.75 от «сторону → Героев Обороны» отсекается 0.80, при этом
    # 2+ gram (где есть контекст) остаётся 0.75.
    entity_similarity_threshold_1gram: float = 0.80

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

    # ─── Phonetic matching (parser/phonetic_index.py) ────────────────────
    # Включение фонетической стратегии (T2). False — только лемматический fallback.
    phonetic_enabled: bool = True
    # Порог rapidfuzz token_sort_ratio для верификации Metaphone-кандидатов (0-1).
    phonetic_match_threshold: float = 0.85
    # Строгий порог для 1-gram phonetic-матчей (G3). Жёстче основного, чтобы
    # отсечь близкие корни вроде «зелёный → Зелёная Балка».
    phonetic_match_threshold_1gram: float = 0.95
    # Включение лемматического fallback (T3).
    lemma_fallback_enabled: bool = True
    # Максимальный размер n-граммы (количество токенов) для T2.
    max_phonetic_ngram_length: int = 4
    # Сколько словоформ на одно content-слово брать из parse[0].lexeme при сборке индекса.
    phonetic_forms_cap: int = 12
    # Жёсткий cap количества вариантов на одну улицу. При превышении —
    # fallback на «инфлектировать только первое content-слово».
    phonetic_variants_per_street_cap: int = 500

    # ─── Generic-suffix фильтр для 1-gram (G1) ─────────────────────────
    # Слова, которые сами по себе не должны порождать матч улицы. Только в
    # составе 2+ gram. Закрывает FP «возле кладбища → 2 кладбища-улицы».
    generic_suffixes: tuple = (
        'кладбище', 'рынок', 'сквер', 'площадь', 'переулок', 'проспект',
        'шоссе', 'мост', 'парк', 'бульвар', 'набережная', 'дорога',
        'спуск', 'сад', 'треугольник', 'центр', 'район', 'посёлок',
        'улица', 'станция', 'остановка',
    )

    # ─── Razdel-шум: токены, не считаемые «словами» (G2) ──────────────
    # Префильтр перед построением n-grams: эти токены выбрасываются, чтобы
    # не раздувать size и не засорять matched_part.
    punctuation_tokens: tuple = (
        '#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';',
    )

    # ─── Gap-grams (User#3) ────────────────────────────────────────────
    # Максимальный разрыв между токенами при построении gap-n-gram. 0 = только
    # подряд идущие; 3 = «Ольгиевский этот самый спуск» сматчится как 2-gram.
    max_token_gap: int = 3

    # ─── Multi-word confirmation (User#1) ──────────────────────────────
    # Окно поиска недостающего ref-слова многословной улицы вокруг matched n-gram.
    multiword_confirm_window: int = 8
    # Порог rapidfuzz для подтверждения второго ref-слова (мягче основного —
    # фонетика+морфология уже доказали близость, ищем «второе слово где-то рядом»).
    multiword_confirm_threshold: float = 0.70
    # Бонус к raw score (0-100) при подтверждении ref-слова в окне.
    multiword_confirm_bonus: float = 10.0
    # Штраф к raw score (0-100), если ref-слова многословной улицы не найдены.
    # 15.0 калибрировано: perfect 1-gram (raw=100) − 15 = 85, × bias_1g=0.85
    # = 72.25, ниже threshold 75 → партиальные одиночные мнения многословных
    # улиц без контекста отсекаются. Это закрывает FP «зелёный бус → Зелёная
    # Балка/Горка», но и теряет легитимное «на арнаутской» без определителя.
    # Соответствующее партиальное мнение появится только при наличии хотя бы
    # одного ref-слова в окне (тогда bonus компенсирует штраф).
    multiword_unconfirmed_penalty: float = 15.0

    # ─── Per-word scoring + dynamic threshold (User#2) ─────────────────
    # Шаг снижения порога на каждое дополнительное слово в n-grams.
    # threshold = base - (size - 1) * step.
    dynamic_threshold_step: float = 0.03
    # Минимальный per-word порог при покомпонентной оценке (rapidfuzz.ratio
    # каждой пары слов).
    per_word_threshold: float = 0.75
    # Softening rapidfuzz-порога когда Metaphone-код уже совпал (фонетическая
    # идентичность доказана, rapidfuzz отсеивает ложные коллизии).
    metaphone_softening: float = 0.10

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
    history_limit: int = 25

    # Размер asyncio.Queue для входящих сообщений (производитель-потребитель).
    message_queue_maxsize: int = 1000

    # Каталог хранения медиафайлов (фотографии событий). Монтируется через
    # volume в docker-compose, путь синхронизирован с разделом volumes.
    events_media_dir: str = "/app/media/events"

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
