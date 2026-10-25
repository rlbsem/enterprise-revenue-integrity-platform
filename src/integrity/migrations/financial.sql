-- Additive migration: original foundation migrations remain unchanged.
CREATE TABLE source_events(event_id text PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,
 logical_id text NOT NULL,revision integer NOT NULL CHECK(revision>0),body jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant,entity,logical_id,revision));
CREATE TRIGGER immutable_source_event BEFORE UPDATE OR DELETE ON source_events FOR EACH ROW EXECUTE FUNCTION immutable_row();
INSERT INTO schema_version VALUES (2);
