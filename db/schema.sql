-- Schema do AV Jobs — fonte única da verdade.
--
-- Aplicado pelo BOT no boot (bot/store.py -> Store.migrar) e usado pelo PAINEL.
-- Só existe em um lugar de propósito: schema duplicado em dois repositórios de
-- código diverge no primeiro dia em que alguém tem pressa.
--
-- Todo comando aqui precisa ser idempotente: roda a cada deploy do bot.

CREATE TABLE IF NOT EXISTS job_events (
    id            BIGSERIAL PRIMARY KEY,
    uid           TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    title         TEXT        NOT NULL DEFAULT '',
    company       TEXT        NOT NULL DEFAULT '',
    url           TEXT        NOT NULL DEFAULT '',
    status        TEXT        NOT NULL,
    category      TEXT        NOT NULL DEFAULT '',
    work_mode     TEXT        NOT NULL DEFAULT '',
    role_type     TEXT        NOT NULL DEFAULT '',
    salary        TEXT        NOT NULL DEFAULT '',
    score         INTEGER     NOT NULL DEFAULT 0,
    language      TEXT        NOT NULL DEFAULT '',
    seniority     TEXT        NOT NULL DEFAULT '',
    reason        TEXT        NOT NULL DEFAULT '',
    published_at  TEXT        NOT NULL DEFAULT '',
    local_day     DATE        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Colunas acrescentadas depois da primeira versão. Ficam como ALTER, e não
-- dentro do CREATE acima, porque o CREATE só roda em banco novo: em banco que
-- já existe ele é ignorado pelo IF NOT EXISTS e as colunas nunca apareceriam.
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS categoria    TEXT    NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;
-- Quando a vaga saiu do ar na plataforma de origem. NULL = ainda aberta (ou
-- fonte sem como verificar, caso do Indeed).
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;

-- O painel quase sempre pergunta "o que aconteceu nos últimos N dias, por
-- status" — daí o índice composto em vez de um por coluna.
CREATE INDEX IF NOT EXISTS job_events_day_status_idx ON job_events (local_day DESC, status);
CREATE INDEX IF NOT EXISTS job_events_source_idx     ON job_events (source, local_day DESC);
CREATE INDEX IF NOT EXISTS job_events_created_idx    ON job_events (created_at DESC);

-- Um mesmo uid pode reaparecer (fila devolvida, reenvio), mas nunca duas vezes
-- no mesmo status: sem isso um redeploy no meio do ciclo duplicaria a métrica.
CREATE UNIQUE INDEX IF NOT EXISTS job_events_uid_status_key ON job_events (uid, status);

CREATE TABLE IF NOT EXISTS bot_config (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    data       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bot_config_single_row CHECK (id = 1)
);

INSERT INTO bot_config (id, data) VALUES (1, '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    name          TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
