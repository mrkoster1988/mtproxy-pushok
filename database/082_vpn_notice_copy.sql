ALTER TABLE vpn_settings ADD COLUMN IF NOT EXISTS notice_copy_version integer NOT NULL DEFAULT 1;
UPDATE vpn_settings SET policy=policy-'expiry_text'-'never_text'-'idle_text',
                        version=version+1, notice_copy_version=2
WHERE notice_copy_version<2;
ALTER TABLE vpn_settings ALTER COLUMN notice_copy_version SET DEFAULT 2;
