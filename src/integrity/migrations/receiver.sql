CREATE TABLE schema_version(version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (1);
CREATE TABLE customers(tenant text NOT NULL,entity text NOT NULL,id text NOT NULL,
 canonical_account text NOT NULL,PRIMARY KEY(tenant,entity,id));
CREATE TABLE records(kind text NOT NULL,id text NOT NULL,tenant text NOT NULL,entity text NOT NULL,
 body jsonb NOT NULL,PRIMARY KEY(kind,id));
CREATE TABLE effects(effect_id uuid PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,
 hash text NOT NULL,payload jsonb NOT NULL,receipt jsonb NOT NULL,committed_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE faults(effect_id uuid PRIMARY KEY,modes jsonb NOT NULL);
CREATE TABLE requests(id bigserial PRIMARY KEY,effect_id uuid NOT NULL,operation text NOT NULL,
 status integer NOT NULL,observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE FUNCTION immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'append_only_record'; END $$;
CREATE TRIGGER immutable_effect BEFORE UPDATE OR DELETE ON effects FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON records FOR EACH ROW EXECUTE FUNCTION immutable_row();
