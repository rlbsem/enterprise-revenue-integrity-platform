ALTER TABLE outbox ADD COLUMN max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 3 AND 6);
ALTER TABLE outbox ADD COLUMN fence bigint NOT NULL DEFAULT 0;
ALTER TABLE outbox ADD COLUMN lease_owner uuid;
ALTER TABLE outbox ADD COLUMN lease_until timestamptz;
CREATE TABLE operator_scope(db_role name NOT NULL,actor text NOT NULL,role text NOT NULL,
 tenant text NOT NULL,entity text NOT NULL,PRIMARY KEY(db_role,tenant,entity));
CREATE TABLE operator_users(username text PRIMARY KEY,db_role name NOT NULL,password_hash text NOT NULL,active boolean NOT NULL DEFAULT true);
CREATE TABLE sessions(session_hash text PRIMARY KEY,username text NOT NULL REFERENCES operator_users,
 csrf_hash text NOT NULL,expires_at timestamptz NOT NULL,revoked boolean NOT NULL DEFAULT false);
CREATE TABLE login_attempts(bucket text PRIMARY KEY,failures integer NOT NULL DEFAULT 0,locked_until timestamptz);
CREATE TABLE proposals(id uuid PRIMARY KEY,tenant text NOT NULL,entity text NOT NULL,
 kind text NOT NULL CHECK(kind='retry_effect'),effect_id uuid NOT NULL REFERENCES outbox,
 payload jsonb NOT NULL,payload_hash text NOT NULL,reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
 proposed_by name NOT NULL DEFAULT session_user,state text NOT NULL DEFAULT 'pending'
 CHECK(state IN ('pending','approved','rejected','executed','invalidated')),
 detail text NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '10 minutes');
CREATE TABLE decisions(proposal_id uuid PRIMARY KEY REFERENCES proposals,tenant text NOT NULL,entity text NOT NULL,
 result text NOT NULL CHECK(result IN ('approved','rejected')),actor name NOT NULL DEFAULT session_user,
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),created_at timestamptz NOT NULL DEFAULT clock_timestamp());
ALTER TABLE outbox ADD COLUMN authorization_proposal uuid REFERENCES proposals;
CREATE TRIGGER immutable_decision BEFORE UPDATE OR DELETE ON decisions FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE FUNCTION scoped(t text,e text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=public,pg_temp AS $$ SELECT EXISTS(SELECT 1 FROM public.operator_scope
 WHERE db_role=session_user AND (tenant=t OR tenant='*') AND (entity=e OR entity='*')
 AND (role='worker' OR EXISTS(SELECT 1 FROM public.operator_users u WHERE u.db_role=operator_scope.db_role AND u.active))) $$;
REVOKE ALL ON FUNCTION scoped(text,text) FROM PUBLIC;
CREATE FUNCTION guard_proposal() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE binding boolean; effect public.outbox%ROWTYPE;
BEGIN
 SELECT EXISTS(SELECT 1 FROM public.operator_scope WHERE db_role=session_user AND role='operator'
 AND tenant=NEW.tenant AND entity=NEW.entity AND EXISTS(SELECT 1 FROM public.operator_users WHERE db_role=session_user AND active)) INTO binding;
 IF NOT binding OR NEW.proposed_by<>session_user OR NEW.state<>'pending' OR NEW.expires_at>clock_timestamp()+interval '10 minutes'
 OR NEW.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'operator_proposal_permission_denied'; END IF;
 SELECT * INTO effect FROM public.outbox WHERE effect_id=NEW.effect_id AND tenant=NEW.tenant AND entity=NEW.entity;
 IF NOT FOUND OR effect.status<>'exhausted' OR effect.attempts<>3 OR effect.max_attempts<>3
 OR NEW.payload IS DISTINCT FROM jsonb_build_object('payload_hash',effect.hash,'effect_id',effect.effect_id::text,
 'expected_attempts',effect.attempts,'source_revision',effect.payload->'source_revision','grant_max_attempts',6)
 THEN RAISE EXCEPTION 'proposal_effect_not_eligible'; END IF;
 INSERT INTO public.audit(tenant,entity,action,reference,detail) VALUES(NEW.tenant,NEW.entity,'operator_proposed',NEW.id::text,jsonb_build_object('actor',session_user,'payload_hash',NEW.payload_hash));
 RETURN NEW;
END $$;
CREATE TRIGGER valid_proposal BEFORE INSERT ON proposals FOR EACH ROW EXECUTE FUNCTION guard_proposal();
CREATE FUNCTION frozen_proposal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF ROW(NEW.id,NEW.tenant,NEW.entity,NEW.kind,NEW.effect_id,NEW.payload,NEW.payload_hash,NEW.reason,NEW.proposed_by,NEW.created_at,NEW.expires_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.tenant,OLD.entity,OLD.kind,OLD.effect_id,OLD.payload,OLD.payload_hash,OLD.reason,OLD.proposed_by,OLD.created_at,OLD.expires_at)
 THEN RAISE EXCEPTION 'immutable_proposal_content'; END IF; RETURN NEW; END $$;
CREATE TRIGGER immutable_proposal BEFORE UPDATE ON proposals FOR EACH ROW EXECUTE FUNCTION frozen_proposal();
CREATE FUNCTION guard_decision() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE proposal public.proposals%ROWTYPE;
BEGIN
 IF NEW.actor<>session_user OR NOT EXISTS(SELECT 1 FROM public.operator_scope WHERE db_role=session_user AND role='approver'
 AND tenant=NEW.tenant AND entity=NEW.entity AND EXISTS(SELECT 1 FROM public.operator_users WHERE db_role=session_user AND active)) THEN RAISE EXCEPTION 'approver_permission_denied'; END IF;
 SELECT * INTO proposal FROM public.proposals WHERE id=NEW.proposal_id FOR UPDATE;
 IF NOT FOUND OR proposal.tenant<>NEW.tenant OR proposal.entity<>NEW.entity OR proposal.proposed_by=NEW.actor
 OR proposal.state<>'pending' OR proposal.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'decision_scope_or_separation_denied'; END IF;
 UPDATE public.proposals SET state=NEW.result WHERE id=NEW.proposal_id;
 INSERT INTO public.audit(tenant,entity,action,reference,detail) VALUES(NEW.tenant,NEW.entity,'operator_'||NEW.result,NEW.proposal_id::text,jsonb_build_object('actor',session_user,'payload_hash',proposal.payload_hash));
 RETURN NEW;
END $$;
CREATE TRIGGER valid_decision BEFORE INSERT ON decisions FOR EACH ROW EXECUTE FUNCTION guard_decision();
DO $$ DECLARE table_name text; BEGIN
 FOREACH table_name IN ARRAY ARRAY['accounts','mappings','inbox','outbox','exceptions','audit','financial_runs','financial_cases','financial_case_events','proposals','decisions'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name);
 EXECUTE format('CREATE POLICY scope_policy ON %I USING(public.scoped(tenant,entity)) WITH CHECK(public.scoped(tenant,entity))',table_name);
 END LOOP;
END $$;
ALTER TABLE attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY attempt_scope ON attempts USING(EXISTS(SELECT 1 FROM outbox o WHERE o.effect_id=attempts.effect_id));
INSERT INTO schema_version(version) VALUES(3);
