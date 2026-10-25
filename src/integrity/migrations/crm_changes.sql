CREATE FUNCTION immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'append_only_source_event'; END $$;
CREATE TRIGGER immutable_crm_event BEFORE UPDATE OR DELETE ON events FOR EACH ROW EXECUTE FUNCTION immutable_row();
INSERT INTO schema_version VALUES(2);
