"""Public errors whose messages never expose SQL, credentials or provider responses."""

_STATUS = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "INVALID_STATE": 409,
    "DEPENDENCY_MISSING": 409,
    "STALE_DEPENDENCY": 409,
    "INVALID_REQUEST": 422,
    "EVIDENCE_UNAVAILABLE": 503,
    "PROTOCOL_UPGRADE_REQUIRED": 409,
    "PROTOCOL_NOT_SUPPORTED": 409,
    "METHOD_PROFILE_UNSUPPORTED": 409,
    "ACTION_NOT_SUPPORTED_FOR_PROTOCOL": 409,
    "PROFILE_CONTENT_CONFLICT": 409,
    "PROTOCOL_POLICY_MISSING": 409,
    "PROTOCOL_BINDING_MISSING": 409,
    "PROTOCOL_BINDING_CONFLICT": 409,
    "PROTOCOL_WRITE_DISABLED": 409,
    "COMPOSITION_INPUT_CHANGED": 409,
    "COMPOSITION_NOT_READY": 409,
    "CONFIRMATION_INCOMPLETE": 409,
    "EXECUTION_AUTHORITY_EXPIRED": 409,
    "ACCEPTANCE_APPOINTMENT_EXPIRED": 409,
}

_MESSAGES = {
    "UNAUTHENTICATED": "A valid bearer credential is required.",
    "FORBIDDEN": "Current authority does not permit this operation.",
    "NOT_FOUND": "The requested record is unavailable.",
    "VERSION_CONFLICT": "An object version has changed.",
    "IDEMPOTENCY_CONFLICT": "The idempotency key identifies a different request.",
    "INVALID_STATE": "The current state does not permit this operation.",
    "DEPENDENCY_MISSING": "A required dependency version is missing.",
    "STALE_DEPENDENCY": "A referenced dependency is no longer effective.",
    "INVALID_REQUEST": "The request does not satisfy the command schema.",
    "EVIDENCE_UNAVAILABLE": "Required evidence could not be verified.",
    "PROTOCOL_UPGRADE_REQUIRED": "The target is registered under a newer protocol; the request must declare that contract version.",
    "PROTOCOL_NOT_SUPPORTED": "The declared or registered protocol is not supported here.",
    "METHOD_PROFILE_UNSUPPORTED": "The bound method profile is not installed or not supported.",
    "ACTION_NOT_SUPPORTED_FOR_PROTOCOL": "This action has no handler for the object's registered protocol.",
    "PROFILE_CONTENT_CONFLICT": "The same profile id and revision were installed with different content.",
    "PROTOCOL_POLICY_MISSING": "No server-side protocol policy registers this creation scope.",
    "PROTOCOL_BINDING_MISSING": "The object has no server-side protocol registration.",
    "PROTOCOL_BINDING_CONFLICT": "A referenced object is registered under a conflicting protocol.",
    "PROTOCOL_WRITE_DISABLED": "Protocol writes are disabled by the current server-side registry.",
    "COMPOSITION_INPUT_CHANGED": "Composition inputs or versions do not match the candidate manifest.",
    "COMPOSITION_NOT_READY": "Composition is not yet ready to confirm or activate.",
    "CONFIRMATION_INCOMPLETE": "Composition confirmation set is not complete.",
    "EXECUTION_AUTHORITY_EXPIRED": "The execution authority window has expired.",
    "ACCEPTANCE_APPOINTMENT_EXPIRED": "The acceptance appointment window has expired.",
}


class GovernedError(Exception):
    def __init__(self, code: str, message: str = "", status: int | None = None):
        self.code = code
        self.message = message or _MESSAGES.get(code, "The governed operation failed.")
        self.status = status if status is not None else _STATUS.get(code, 500)
        self.status_code = self.status
        super().__init__(self.message)
