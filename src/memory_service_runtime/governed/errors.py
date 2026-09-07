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
}


class GovernedError(Exception):
    def __init__(self, code: str, message: str = "", status: int | None = None):
        self.code = code
        self.message = message or _MESSAGES.get(code, "The governed operation failed.")
        self.status = status if status is not None else _STATUS.get(code, 500)
        self.status_code = self.status
        super().__init__(self.message)
