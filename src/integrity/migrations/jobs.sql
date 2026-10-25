CREATE TABLE reconciliation_jobs(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant text NOT NULL,entity text NOT NULL,
 proposal_id uuid UNIQUE REFERENCES proposals,created_by name NOT NULL DEFAULT session_user,reason text NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','verified','failed')),
 fence bigint NOT NULL DEFAULT 0,detail text NOT NULL DEFAULT '',source_hash text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE UNIQUE INDEX one_active_reconciliation ON reconciliation_jobs(tenant,entity) WHERE state IN ('queued','running');
ALTER TABLE reconciliation_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY job_scope ON reconciliation_jobs USING(public.scoped(tenant,entity)) WITH CHECK(public.scoped(tenant,entity));
CREATE FUNCTION guard_recheck() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.operator_scope WHERE db_role=session_user AND role='worker') THEN RETURN NEW; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.operator_scope s JOIN public.operator_users u USING(db_role)
 WHERE s.db_role=session_user AND s.role='operator' AND u.active AND s.tenant=NEW.tenant AND s.entity=NEW.entity)
 OR NEW.created_by<>session_user OR NEW.state<>'queued' OR NEW.fence<>0 OR NEW.source_hash IS NOT NULL
 OR NEW.proposal_id IS NOT NULL OR NEW.detail<>'' OR length(trim(NEW.reason)) NOT BETWEEN 1 AND 500
 THEN RAISE EXCEPTION 'recheck_permission_or_state_denied'; END IF;
 INSERT INTO public.audit(tenant,entity,action,reference,detail) VALUES(NEW.tenant,NEW.entity,'financial_recheck_requested',NEW.id::text,jsonb_build_object('actor',session_user));
 RETURN NEW;
END $$;
CREATE TRIGGER valid_recheck BEFORE INSERT ON reconciliation_jobs FOR EACH ROW EXECUTE FUNCTION guard_recheck();
INSERT INTO schema_version(version) VALUES(4);
