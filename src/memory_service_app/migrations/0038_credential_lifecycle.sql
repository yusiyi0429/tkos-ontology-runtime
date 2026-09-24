-- 0038: credential lifecycle (#32 batch E).
--
-- Credentials may carry an expiry: authentication refuses a credential whose
-- expires_at has passed.  Existing rows keep NULL, which means no expiry.
-- The control plane can now enumerate, rotate and revoke credentials: the
-- permissive policy used to show a session only the row matching its own
-- credential digest, so even the owner could not revoke a credential without
-- holding its token.  Like gov_scopes and gov_objects in 0018, control-plane
-- sessions (database owner + explicit GUC) see every row; runtime sessions
-- still see only their own credential.
ALTER TABLE gov_credentials ADD COLUMN expires_at timestamptz;

DROP POLICY IF EXISTS gov_credentials_digest ON gov_credentials;
CREATE POLICY gov_credentials_digest ON gov_credentials
    USING (gov_credential_matches(credential_digest) OR gov_control_plane_on())
    WITH CHECK (gov_credential_matches(credential_digest) OR gov_control_plane_on());
