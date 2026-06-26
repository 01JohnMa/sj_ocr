-- ============================================================
-- Normalize GoTrue token fields for seeded auth users
-- ============================================================
-- Some GoTrue versions scan these nullable columns into strings.
-- Seeded users must use empty strings instead of NULL to avoid auth 500s.
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
    required_token_columns text[] := ARRAY[
        'confirmation_token',
        'recovery_token',
        'email_change_token_new',
        'email_change_token_current',
        'reauthentication_token'
    ];
    missing_token_columns text;
BEGIN
    SELECT string_agg(required_column, ', ' ORDER BY required_column)
    INTO missing_token_columns
    FROM unnest(required_token_columns) AS required_column
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'auth'
          AND table_name = 'users'
          AND column_name = required_column
    );

    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'auth'
          AND table_name = 'users'
    ) AND missing_token_columns IS NULL THEN
        UPDATE auth.users
        SET
            confirmation_token = COALESCE(confirmation_token, ''),
            recovery_token = COALESCE(recovery_token, ''),
            email_change_token_new = COALESCE(email_change_token_new, ''),
            email_change_token_current = COALESCE(email_change_token_current, ''),
            reauthentication_token = COALESCE(reauthentication_token, '')
        WHERE
            id = ANY(seed_user_ids)
            AND (
                confirmation_token IS NULL
                OR recovery_token IS NULL
                OR email_change_token_new IS NULL
                OR email_change_token_current IS NULL
                OR reauthentication_token IS NULL
            );

        RAISE NOTICE '017: seeded auth.users token NULL values normalized';
    ELSE
        RAISE NOTICE '017: auth.users or required token columns missing (missing: %), skipping token normalization',
            COALESCE(missing_token_columns, 'auth.users');
    END IF;
END $$;
