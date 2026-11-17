-- Автосоздание ограничено существующим VPS. Секреты остаются только на хосте.
CREATE TABLE IF NOT EXISTS proxy_provisioning_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled BOOLEAN NOT NULL DEFAULT false,
 max_instances INTEGER NOT NULL DEFAULT 2 CHECK(max_instances BETWEEN 1 AND 3),
 wait_minutes INTEGER NOT NULL DEFAULT 120 CHECK(wait_minutes BETWEEN 90 AND 10080),
 first_port INTEGER NOT NULL DEFAULT 9443 CHECK(first_port BETWEEN 1024 AND 65533),
 revision UUID NOT NULL DEFAULT gen_random_uuid(),
 updated_by UUID REFERENCES users(id),
 evaluated_at TIMESTAMPTZ,
 decision TEXT NOT NULL DEFAULT 'disabled',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO proxy_provisioning_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS proxy_provisioning_jobs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 proxy_node_id UUID UNIQUE NOT NULL REFERENCES proxy_nodes(id) ON DELETE RESTRICT,
 owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 policy_revision UUID NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','active','failed')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
 retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS proxy_provisioning_one_pending
 ON proxy_provisioning_jobs((true)) WHERE state='pending';
