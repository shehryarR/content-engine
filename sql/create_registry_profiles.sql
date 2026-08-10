
CREATE TABLE IF NOT EXISTS identity_profiles (
    identity_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    refrence_asset TEXT NOT NULL,
    consent_grant_id TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS voice_profiles (
    voice_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    refrence_sample_id TEXT NOT NULL,
    consent_grant_id TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);