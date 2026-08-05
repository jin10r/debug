-- 10-type-config.sql
-- Таблицы конфигурации для TypeValidator и StrategySelector
-- Загружаются из CSV и кэшируются в Python при initialize().

-- Описания типов geo-объектов для zero-shot BERT классификатора.
-- Каждый тип описывается короткой фразой на русском — эмбеддинг считается
-- при warmup и используется для cosine-similarity zero-shot.
CREATE TABLE IF NOT EXISTS geo_type_descriptions (
    type TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

INSERT INTO geo_type_descriptions (type, description) VALUES
    ('street',    'городская улица, проспект, бульвар, переулок, шоссе, проезд'),
    ('village',   'село, деревня, посёлок, населённый пункт, сельская местность'),
    ('town',      'город, ПГТ, крупный населённый центр, муниципалитет'),
    ('station',   'железнодорожная станция, остановка, вокзал, платформа'),
    ('park',      'парк, сквер, сад, зона отдыха, зелёная зона'),
    ('landmark',  'достопримечательность, памятник, здание, сооружение, ориентир'),
    ('market',    'рынок, торговый центр, базар, ярмарка'),
    ('square',    'площадь, центральная площадь города'),
    ('bridge',    'мост, путепровод, эстакада, виадук'),
    ('embankment','набережная, берег реки, прибрежная зона'),
    ('district',  'район, административный район, микрорайон, квартал'),
    ('stop',      'остановка общественного транспорта, автобусная остановка'),
    ('beach',     'пляж, побережье, купальная зона'),
    ('forest',    'лес, лесопарк, лесополоса, роща'),
    ('water',     'водоём, озеро, пруд, река, море, лиман')
ON CONFLICT (type) DO UPDATE SET description = EXCLUDED.description;

-- Роль geo-объекта относительно текста: какие предлоги/контекст указывают
-- на конкретную роль (source, destination, via, landmark).
CREATE TABLE IF NOT EXISTS geo_role_patterns (
    role TEXT PRIMARY KEY,
    prepositions TEXT[] NOT NULL,
    patterns TEXT[] NOT NULL DEFAULT '{}'
);

INSERT INTO geo_role_patterns (role, prepositions, patterns) VALUES
    ('source',      ARRAY['из', 'от', 'с', 'со', 'из-за', 'из-под'], '{}'),
    ('destination', ARRAY['до', 'к', 'ко', 'в', 'на', 'за'], '{}'),
    ('via',         ARRAY['через', 'сквозь', 'по', 'мимо'], '{}'),
    ('landmark',    ARRAY['у', 'возле', 'около', 'рядом с', 'недалеко от', 'напротив', 'напротив'], '{}')
ON CONFLICT (role) DO UPDATE SET
    prepositions = EXCLUDED.prepositions,
    patterns = EXCLUDED.patterns;

-- Какие типы объектов разрешены для каждой стратегии.
-- Используется в process_candidates для фильтрации типов.
CREATE TABLE IF NOT EXISTS strategy_type_filters (
    strategy TEXT PRIMARY KEY,
    allowed_types TEXT[] NOT NULL
);

INSERT INTO strategy_type_filters (strategy, allowed_types) VALUES
    ('single_match', ARRAY['street', 'village', 'town', 'station', 'park', 'landmark', 'market', 'square', 'bridge', 'embankment', 'district', 'stop', 'beach', 'forest', 'water']),
    ('intersection', ARRAY['street', 'park', 'landmark', 'bridge']),
    ('midpoint',     ARRAY['street', 'market', 'station', 'park', 'landmark'])
ON CONFLICT (strategy) DO UPDATE SET
    allowed_types = EXCLUDED.allowed_types;

-- Слой → типы geo-объектов, релевантных для этого слоя.
-- Используется как дополнительный сигнал для выбора стратегии.
CREATE TABLE IF NOT EXISTS layer_geo_types (
    layer TEXT PRIMARY KEY,
    relevant_types TEXT[] NOT NULL
);

INSERT INTO layer_geo_types (layer, relevant_types) VALUES
    ('traffic', ARRAY['street', 'bridge', 'intersection']),
    ('bus',     ARRAY['street', 'stop', 'station']),
    ('cops',    ARRAY['street', 'landmark', 'district']),
    ('pig',     ARRAY['village', 'forest', 'water', 'beach', 'park'])
ON CONFLICT (layer) DO UPDATE SET
    relevant_types = EXCLUDED.relevant_types;
