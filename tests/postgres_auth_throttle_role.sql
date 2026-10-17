-- Disposable CI only. Never provision real credentials from this script.
CREATE ROLE gather_throttle_ci LOGIN PASSWORD 'only-for-disposable-ci-tests'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT CONNECT ON DATABASE gather_contract TO gather_throttle_ci;
GRANT USAGE ON SCHEMA public TO gather_throttle_ci;
GRANT SELECT, INSERT, DELETE ON auth_rate_hits TO gather_throttle_ci;
GRANT USAGE, SELECT ON SEQUENCE auth_rate_hits_id_seq TO gather_throttle_ci;

DO $$
BEGIN
  IF NOT has_table_privilege('gather_throttle_ci', 'auth_rate_hits', 'INSERT')
     OR has_table_privilege('gather_throttle_ci', 'projects', 'INSERT')
     OR has_table_privilege('gather_throttle_ci', 'sessions', 'SELECT')
     OR has_table_privilege('gather_throttle_ci', 'auth_rate_hits', 'UPDATE')
  THEN
    RAISE EXCEPTION 'throttle role privileges too broad or incomplete';
  END IF;
END $$;
