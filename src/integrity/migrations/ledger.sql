CREATE TABLE financial_runs(id uuid PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,source_hash text NOT NULL,
 period jsonb NOT NULL,report jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER immutable_financial_run BEFORE UPDATE OR DELETE ON financial_runs FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TABLE financial_cases(id uuid PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,category text NOT NULL,
 owner text NOT NULL,state text NOT NULL CHECK(state IN ('open','explained','reconciled')),amount_cents bigint,
 last_run uuid NOT NULL REFERENCES financial_runs,detail jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE financial_case_events(id bigserial PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,
 case_id uuid NOT NULL REFERENCES financial_cases,run_id uuid NOT NULL REFERENCES financial_runs,state text NOT NULL,
 detail jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER immutable_case_event BEFORE UPDATE OR DELETE ON financial_case_events FOR EACH ROW EXECUTE FUNCTION immutable_row();
INSERT INTO schema_version(version) VALUES(2);
