\set ON_ERROR_STOP on
\getenv api_password POSTGREST_DB_PASSWORD

CREATE ROLE authenticator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'api_password';
CREATE ROLE ods_reader NOLOGIN;
CREATE ROLE ods_writer NOLOGIN;
GRANT ods_reader, ods_writer TO authenticator;
REVOKE ALL ON DATABASE ods FROM PUBLIC;
GRANT CONNECT ON DATABASE ods TO authenticator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA api;
GRANT USAGE ON SCHEMA api TO ods_reader, ods_writer;

CREATE TABLE api.artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 500),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON api.artifacts TO ods_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON api.artifacts TO ods_writer;
ALTER ROLE ods_reader SET statement_timeout = '5s';
ALTER ROLE ods_writer SET statement_timeout = '5s';
