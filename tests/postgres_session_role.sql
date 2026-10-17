-- Test-only database identity for the future session subsystem.
-- Never provision production identities from this SQL file.
CREATE ROLE gather_session_ci LOGIN PASSWORD 'disposable-session-test-only'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT CONNECT ON DATABASE gather_contract TO gather_session_ci;
GRANT USAGE ON SCHEMA public TO gather_session_ci;
GRANT SELECT ON members TO gather_session_ci;
GRANT SELECT, INSERT, DELETE ON sessions TO gather_session_ci;

DO $$
BEGIN
 IF NOT has_table_privilege('gather_session_ci','sessions','SELECT')
    OR NOT has_table_privilege('gather_session_ci','sessions','INSERT')
    OR NOT has_table_privilege('gather_session_ci','sessions','DELETE')
    OR NOT has_table_privilege('gather_session_ci','members','SELECT')
    OR has_table_privilege('gather_session_ci','members','UPDATE')
    OR has_table_privilege('gather_session_ci','users','SELECT')
    OR has_table_privilege('gather_session_ci','projects','SELECT')
    OR has_table_privilege('gather_session_ci','sessions','UPDATE')
 THEN RAISE EXCEPTION 'Insecure session role grants'; END IF;
END $$;
