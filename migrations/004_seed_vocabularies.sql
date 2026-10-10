-- Correlia: controlled vocabularies

CREATE TABLE catalog.vocab_geo_level (
    value TEXT PRIMARY KEY,
    description TEXT
);
INSERT INTO catalog.vocab_geo_level VALUES
    ('country', 'Sovereign state or equivalent (ISO 3166-1)'),
    ('admin1',  'First-level subnational (state, province, region)'),
    ('admin2',  'Second-level subnational (county, district)'),
    ('city',    'City, town, or urban agglomeration');

CREATE TABLE catalog.vocab_collection_method (
    value TEXT PRIMARY KEY,
    description TEXT
);
INSERT INTO catalog.vocab_collection_method VALUES
    ('census',          'Full population enumeration'),
    ('survey',          'Sampled survey with known design'),
    ('administrative',  'Administrative records (tax, registration, etc.)'),
    ('model',           'Statistical or machine-learning model output'),
    ('satellite',       'Satellite or remote-sensing derived'),
    ('other',           'Other method (see notes)');

CREATE TABLE catalog.vocab_aggregation_method (
    value TEXT PRIMARY KEY,
    description TEXT
);
INSERT INTO catalog.vocab_aggregation_method VALUES
    ('sum',                 'Sum of component values'),
    ('mean',                'Unweighted mean'),
    ('pop_weighted_mean',   'Population-weighted mean'),
    ('median',              'Median'),
    ('rate',                'Direct rate (numerator/denominator)'),
    ('none',                'Not an aggregate');

CREATE TABLE catalog.vocab_quality_dimension (
    value TEXT PRIMARY KEY,
    description TEXT
);
INSERT INTO catalog.vocab_quality_dimension VALUES
    ('relevance',      'Degree to which data meets user needs'),
    ('accuracy',       'Closeness of values to the truth'),
    ('timeliness',     'Lag between reference period and publication'),
    ('comparability',  'Comparability across places and time'),
    ('coherence',      'Consistency with related indicators'),
    ('accessibility',  'Ease of retrieval and clarity of documentation'),
    ('lineage',        'Traceability of source material and derivation'),
    ('completeness',   'Coverage of expected geographic and temporal units');
