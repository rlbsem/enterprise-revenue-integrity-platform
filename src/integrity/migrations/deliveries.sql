CREATE TABLE source_deliveries(id bigserial PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,body jsonb NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER immutable_delivery BEFORE UPDATE OR DELETE ON source_deliveries FOR EACH ROW EXECUTE FUNCTION immutable_row();
INSERT INTO schema_version VALUES(3);
