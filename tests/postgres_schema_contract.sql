-- Executed only against disposable CI PostgreSQL.
-- Admin fixture seeds legal rows, then switches to restricted runtime role.
INSERT INTO users (id, name, email, password)
VALUES ('u-a','Tenant A Owner','a@example.test','hashed'),
       ('u-b','Tenant B Owner','b@example.test','hashed');

INSERT INTO workspaces (id,name) VALUES ('w-a','A'),('w-b','B');
INSERT INTO members (workspace_id,user_id,role)
VALUES ('w-a','u-a','owner'),('w-b','u-b','owner');
INSERT INTO projects (id,workspace_id,name,description,color,created)
VALUES ('p-a','w-a','A project','','orange',1),
       ('p-b','w-b','B project','','blue',1);
INSERT INTO tasks
 (id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created)
VALUES
 ('t-a','w-a','p-a','A task','','todo','high','u-a','',1),
 ('t-b','w-b','p-b','B task','','todo','high','u-b','',1);

DO $$
BEGIN
  BEGIN
    INSERT INTO tasks
      (id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created)
    VALUES ('t-foreign-project','w-a','p-b','invalid','','todo','high',NULL,'',1);
    RAISE EXCEPTION 'cross-workspace project was accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO tasks
      (id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created)
    VALUES ('t-foreign-assignee','w-a','p-a','invalid','','todo','high','u-b','',1);
    RAISE EXCEPTION 'cross-workspace assignee was accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO sessions (token,user_id,workspace_id,csrf,expires)
    VALUES ('foreign-session','u-b','w-a','csrf',10000);
    RAISE EXCEPTION 'cross-workspace session was accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO tasks
      (id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created)
    VALUES ('invalid-status','w-a','p-a','invalid','','unknown','high',NULL,'',1);
    RAISE EXCEPTION 'invalid task status was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

CREATE ROLE gather_ci_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO gather_ci_runtime;
GRANT SELECT ON projects, tasks, members, workspaces TO gather_ci_runtime;
GRANT INSERT ON tasks TO gather_ci_runtime;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gather_ci_runtime'
             AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'runtime role unexpectedly bypasses RLS';
  END IF;
END $$;

SET ROLE gather_ci_runtime;

-- Fail closed without tenant context.
DO $$
DECLARE count_all integer;
BEGIN
  SELECT count(*) INTO count_all FROM projects;
  IF count_all <> 0 THEN
    RAISE EXCEPTION 'RLS leaked project rows without tenant context';
  END IF;
END $$;

BEGIN;
SET LOCAL app.workspace_id = 'w-a';

DO $$
DECLARE count_a integer;
DECLARE count_b integer;
BEGIN
  SELECT count(*) INTO count_a FROM projects WHERE workspace_id='w-a';
  SELECT count(*) INTO count_b FROM projects WHERE workspace_id='w-b';
  IF count_a <> 1 OR count_b <> 0 THEN
    RAISE EXCEPTION 'RLS failed to isolate projects: %, %', count_a, count_b;
  END IF;
  SELECT count(*) INTO count_a FROM tasks WHERE workspace_id='w-a';
  SELECT count(*) INTO count_b FROM tasks WHERE workspace_id='w-b';
  IF count_a <> 1 OR count_b <> 0 THEN
    RAISE EXCEPTION 'RLS failed to isolate tasks: %, %', count_a, count_b;
  END IF;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO tasks
      (id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created)
    VALUES ('t-rls-denied','w-b','p-b','invalid','','todo','high',NULL,'',1);
    RAISE EXCEPTION 'cross-workspace RLS insert was accepted';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;

-- Positive control proves the policy is not universally denying.
INSERT INTO tasks
  (id,workspace_id,project_id,title,description,status,priority,assignee_id,due,created)
VALUES ('t-rls-allowed','w-a','p-a','allowed','','todo','high',NULL,'',1);

ROLLBACK;
RESET ROLE;

-- A strict runtime role with no auth bootstrap grant is intentional:
-- establishing trusted subject+workspace identity remains a separate gate.
