"""The catalog of audited events. `<subject>.<what happened>`, lower case."""


class Action:
    # --- Authentication and account lifecycle ---
    USER_REGISTERED = "user.registered"
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    REFRESH_REUSE_DETECTED = "auth.refresh_reuse_detected"  # a rotated token came back: likely stolen
    REFRESH_DENIED = "auth.refresh_denied"
    ACCOUNT_ACTIVATED = "account.activated"
    EMAIL_VERIFIED = "email.verified"
    PASSWORD_RESET_REQUESTED = "password.reset_requested"
    PASSWORD_RESET = "password.reset"
    PASSWORD_CHANGED = "password.changed"
    PASSWORD_CHANGE_FAILED = "password.change_failed"

    # --- Users ---
    RECYCLER_VERIFIED = "recycler.verified"
    RECYCLER_REJECTED = "recycler.rejected"
    USER_UPDATED = "user.updated"
    USER_ROLE_CHANGED = "user.role_changed"

    # --- Weighings ---
    WEIGHING_CREATED = "weighing.created"
    WEIGHING_VALIDATED = "weighing.validated"
    WEIGHING_REJECTED = "weighing.rejected"
    WEIGHING_PAID = "weighing.paid"

    # --- Transactions ---
    TRANSACTION_CREATED = "transaction.created"
    TRANSACTION_CANCELLED = "transaction.cancelled"
    TRANSACTION_DELIVERED = "transaction.delivered"
    TRANSACTION_PAID = "transaction.paid"

    # --- Inventory ---
    INVENTORY_UPDATED = "inventory.updated"


SUCCESS = "success"
FAILURE = "failure"
