-- ============================================================
-- Normalize nullable GoTrue email-change field for seeded auth users
-- ============================================================
-- GoTrue v2.143 scans auth.users.email_change into a Go string.
-- Seeded users must use an empty string instead of NULL for that field.
-- ============================================================

DO $$
DECLARE
    -- Keep this list in sync with auth.users ids in 004_seed_users.sql.
    seed_user_ids uuid[] := ARRAY[
        '4377360e-3965-497b-a333-044398d1d0f5'::uuid,
        'bc350d6f-edfd-488d-a9c5-1e6b1d9e5305'::uuid,
        '5484211a-7bb5-4f5c-bb4f-5fb13b2b83aa'::uuid,
        '68ac78c2-9e7d-4071-bda0-02bda6e0ce32'::uuid,
        'cd56d93b-7d74-4194-b019-059117e68035'::uuid,
        'd307e0b5-96f2-4045-b567-fcafb94bf13d'::uuid,
        '9a85d26a-12aa-497f-b605-b600318a256f'::uuid,
        'c6c8cad7-42d0-4b48-9d86-bd40a558b1fb'::uuid
    ];
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'auth'
          AND table_name = 'users'
    ) AND EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'auth'
          AND table_name = 'users'
          AND column_name = 'email_change'
    ) THEN
        UPDATE auth.users
        SET
            email_change = COALESCE(email_change, '')
        WHERE
            id = ANY(seed_user_ids)
            AND email_change IS NULL;

        RAISE NOTICE '018: seeded auth.users email_change NULL values normalized';
    ELSE
        RAISE NOTICE '018: auth.users or email_change column missing, skipping email_change normalization';
    END IF;
END $$;
