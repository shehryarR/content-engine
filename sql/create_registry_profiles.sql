CREATE TABLE IF NOT EXISTS identity_profiles (
    identity_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    reference_asset TEXT NOT NULL,
    reference_sample_hash TEXT NOT NULL,
    consent_grant_id TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS voice_profiles (
    voice_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    provider_voice_id TEXT NOT NULL,
    consent_grant_id TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);