-- Disposable CI credentials, never suitable for production.
CREATE ROLE gather_identity_ci LOGIN PASSWORD 'disposable-identity-test-only'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT CONNECT ON DATABASE gather_contract TO gather_identity_ci;
GRANT USAGE ON SCHEMA public TO gather_identity_ci;
GRANT SELECT (id,email,password) ON users TO gather_identity_ci;
GRANT SELECT (workspace_id,user_id,revoked_at) ON members TO gather_identity_ci;
GRANT INSERT (token,user_id,workspace_id,csrf,expires) ON sessions TO gather_identity_ci;

DO $$
BEGIN
 IF NOT has_column_privilege('gather_identity_ci','users','password','SELECT')
    OR has_column_privilege('gather_identity_ci','users','name','SELECT')
    OR has_table_privilege('gather_identity_ci','users','UPDATE')
    OR has_table_privilege('gather_identity_ci','projects','SELECT')
    OR has_table_privilege('gather_identity_ci','sessions','DELETE')
    OR has_table_privilege('gather_identity_ci','members','UPDATE')
 THEN RAISE EXCEPTION 'Unsafe credential-to-session role privileges'; END IF;
END $$;
