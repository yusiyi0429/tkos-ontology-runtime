-- Keep all existing object types and histories; add the versioned analysis type.
DO $$
DECLARE old_definition text;
BEGIN
  SELECT pg_get_constraintdef(oid) INTO old_definition FROM pg_constraint
    WHERE conrelid='gov_objects'::regclass AND conname='ck_gov_object_type';
  IF old_definition IS NULL THEN
    RAISE EXCEPTION 'expected governed object type constraint';
  END IF;
  ALTER TABLE gov_objects DROP CONSTRAINT ck_gov_object_type;
  EXECUTE 'ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (' ||
    substring(old_definition from 7) || ' OR object_type = ''ResearchBrief'')';
END $$;

CREATE INDEX IF NOT EXISTS ix_gov_protocol_registry_contract
  ON gov_protocol_support_registry(scope_id, protocol_id, contract_version, registry_seq DESC);
