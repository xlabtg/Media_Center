CREATE SCHEMA IF NOT EXISTS nmc_dev;

CREATE TABLE IF NOT EXISTS nmc_dev.tenants (
    tenant_id UUID PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nmc_dev.participants (
    participant_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES nmc_dev.tenants (tenant_id),
    handle TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, handle)
);

CREATE TABLE IF NOT EXISTS nmc_dev.contribution_events (
    contribution_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES nmc_dev.tenants (tenant_id),
    participant_id UUID NOT NULL REFERENCES nmc_dev.participants (participant_id),
    event_type TEXT NOT NULL,
    points INTEGER NOT NULL CHECK (points >= 0),
    source_ref TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nmc_dev.audit_hashes (
    audit_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES nmc_dev.tenants (tenant_id),
    entity_type TEXT NOT NULL,
    entity_id UUID NOT NULL,
    sha256_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nmc_dev_participants_tenant
    ON nmc_dev.participants (tenant_id);

CREATE INDEX IF NOT EXISTS idx_nmc_dev_contribution_events_tenant
    ON nmc_dev.contribution_events (tenant_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_nmc_dev_audit_hashes_tenant
    ON nmc_dev.audit_hashes (tenant_id, entity_type);
