CREATE TABLE schema_version(version integer PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT clock_timestamp());
INSERT INTO schema_version(version) VALUES (1);
CREATE TABLE accounts(
  tenant text NOT NULL, entity text NOT NULL, canonical_account text NOT NULL,
  name text NOT NULL, PRIMARY KEY(tenant,entity,canonical_account)
);
CREATE TABLE mappings(
  tenant text NOT NULL, entity text NOT NULL, canonical_account text NOT NULL,
  source text NOT NULL CHECK(source IN ('salesforce','stripe','netsuite')), source_id text NOT NULL,
  revision integer NOT NULL CHECK(revision>0), status text NOT NULL CHECK(status IN ('approved','revoked')),
  provenance text NOT NULL, PRIMARY KEY(tenant,entity,canonical_account,source,source_id),
  FOREIGN KEY(tenant,entity,canonical_account) REFERENCES accounts
);
CREATE TABLE inbox(
  tenant text NOT NULL, event_id text NOT NULL, entity text NOT NULL,
  opportunity_id text NOT NULL, revision integer NOT NULL CHECK(revision>0),
  hash text NOT NULL, body jsonb NOT NULL, status text NOT NULL,
  detail text NOT NULL DEFAULT '', observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant,event_id), UNIQUE(tenant,opportunity_id,revision)
);
CREATE TABLE source_heads(
 tenant text NOT NULL, opportunity_id text NOT NULL, revision integer NOT NULL, event_id text NOT NULL,
 PRIMARY KEY(tenant,opportunity_id), FOREIGN KEY(tenant,event_id) REFERENCES inbox
);
CREATE TABLE outbox(
 effect_id uuid PRIMARY KEY, tenant text NOT NULL, entity text NOT NULL, event_id text NOT NULL,
 destination text NOT NULL CHECK(destination IN ('stripe','netsuite')), payload jsonb NOT NULL,
 hash text NOT NULL, status text NOT NULL CHECK(status IN ('pending','uncertain','verified','blocked','exhausted')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), next_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 detail text NOT NULL DEFAULT '', proof jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), FOREIGN KEY(tenant,event_id) REFERENCES inbox
);
CREATE TABLE attempts(
 id bigserial PRIMARY KEY, effect_id uuid NOT NULL REFERENCES outbox,
 ordinal integer NOT NULL, stage text NOT NULL, result text NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE exceptions(
 id bigserial PRIMARY KEY, tenant text NOT NULL, entity text NOT NULL, event_id text NOT NULL,
 effect_id uuid REFERENCES outbox, reason text NOT NULL, state text NOT NULL DEFAULT 'open',
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(), FOREIGN KEY(tenant,event_id) REFERENCES inbox
);
CREATE TABLE audit(
 id bigserial PRIMARY KEY, tenant text NOT NULL, entity text NOT NULL, action text NOT NULL,
 reference text NOT NULL, detail jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'append_only_record'; END $$;
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_attempt BEFORE UPDATE OR DELETE ON attempts FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE FUNCTION immutable_intent() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.effect_id,NEW.tenant,NEW.entity,NEW.event_id,NEW.destination,NEW.payload,NEW.hash)
 IS DISTINCT FROM ROW(OLD.effect_id,OLD.tenant,OLD.entity,OLD.event_id,OLD.destination,OLD.payload,OLD.hash)
 THEN RAISE EXCEPTION 'immutable_intent'; END IF; RETURN NEW; END $$;
CREATE TRIGGER immutable_outbox BEFORE UPDATE ON outbox FOR EACH ROW EXECUTE FUNCTION immutable_intent();
CREATE FUNCTION immutable_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.tenant,NEW.event_id,NEW.entity,NEW.opportunity_id,NEW.revision,NEW.hash,NEW.body,NEW.observed_at)
 IS DISTINCT FROM ROW(OLD.tenant,OLD.event_id,OLD.entity,OLD.opportunity_id,OLD.revision,OLD.hash,OLD.body,OLD.observed_at)
 THEN RAISE EXCEPTION 'immutable_source_event'; END IF; RETURN NEW; END $$;
CREATE TRIGGER immutable_inbox BEFORE UPDATE ON inbox FOR EACH ROW EXECUTE FUNCTION immutable_event();
