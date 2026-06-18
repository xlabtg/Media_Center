INSERT INTO nmc_dev.tenants (tenant_id, slug, name, status)
VALUES
    (
        '00000000-0000-4000-8000-000000000001',
        'nmc-pilot',
        'NMC Pilot Tenant',
        'active'
    )
ON CONFLICT (tenant_id) DO UPDATE
SET
    slug = EXCLUDED.slug,
    name = EXCLUDED.name,
    status = EXCLUDED.status;

INSERT INTO nmc_dev.participants (participant_id, tenant_id, handle, role, status)
VALUES
    (
        '00000000-0000-4000-8000-000000000101',
        '00000000-0000-4000-8000-000000000001',
        'council-admin',
        'council',
        'active'
    ),
    (
        '00000000-0000-4000-8000-000000000102',
        '00000000-0000-4000-8000-000000000001',
        'author-1',
        'member',
        'active'
    )
ON CONFLICT (participant_id) DO UPDATE
SET
    tenant_id = EXCLUDED.tenant_id,
    handle = EXCLUDED.handle,
    role = EXCLUDED.role,
    status = EXCLUDED.status;

INSERT INTO nmc_dev.contribution_events (
    contribution_id,
    tenant_id,
    participant_id,
    event_type,
    points,
    source_ref,
    occurred_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000201',
        '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000102',
        'content_publication',
        25,
        'fixture://content/nmc-pilot/post-001',
        '2026-06-18T00:00:00Z'
    ),
    (
        '00000000-0000-4000-8000-000000000202',
        '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000101',
        'governance_review',
        10,
        'fixture://governance/nmc-pilot/review-001',
        '2026-06-18T01:00:00Z'
    )
ON CONFLICT (contribution_id) DO UPDATE
SET
    tenant_id = EXCLUDED.tenant_id,
    participant_id = EXCLUDED.participant_id,
    event_type = EXCLUDED.event_type,
    points = EXCLUDED.points,
    source_ref = EXCLUDED.source_ref,
    occurred_at = EXCLUDED.occurred_at;

INSERT INTO nmc_dev.audit_hashes (
    audit_id,
    tenant_id,
    entity_type,
    entity_id,
    sha256_hash
)
VALUES
    (
        '00000000-0000-4000-8000-000000000301',
        '00000000-0000-4000-8000-000000000001',
        'contribution_event',
        '00000000-0000-4000-8000-000000000201',
        '1111111111111111111111111111111111111111111111111111111111111111'
    ),
    (
        '00000000-0000-4000-8000-000000000302',
        '00000000-0000-4000-8000-000000000001',
        'contribution_event',
        '00000000-0000-4000-8000-000000000202',
        '2222222222222222222222222222222222222222222222222222222222222222'
    )
ON CONFLICT (audit_id) DO UPDATE
SET
    tenant_id = EXCLUDED.tenant_id,
    entity_type = EXCLUDED.entity_type,
    entity_id = EXCLUDED.entity_id,
    sha256_hash = EXCLUDED.sha256_hash;
