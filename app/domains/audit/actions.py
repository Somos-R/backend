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
    PLATFORM_ADMIN_CREATED = "platform_admin.created"

    # --- Backoffice (Somos R's own accounts) ---
    ADMIN_LOGIN = "admin.login"
    ADMIN_LOGIN_FAILED = "admin.login_failed"
    ADMIN_LOGOUT = "admin.logout"
    ADMIN_MFA_ENROLLED = "admin.mfa_enrolled"
    ADMIN_MFA_FAILED = "admin.mfa_failed"
    ADMIN_RECOVERY_CODE_USED = "admin.recovery_code_used"
    ADMIN_RECOVERY_CODES_REGENERATED = "admin.recovery_codes_regenerated"
    ADMIN_MFA_RESET = "admin.mfa_reset"
    ADMIN_AUDIT_VIEWED = "admin.audit_viewed"
    ADMIN_USER_VIEWED = "admin.user_viewed"
    ADMIN_ORGANIZATION_VIEWED = "admin.organization_viewed"

    # --- Users ---
    RECYCLER_VERIFIED = "recycler.verified"
    RECYCLER_REJECTED = "recycler.rejected"
    USER_UPDATED = "user.updated"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_INVITED = "user.invited"
    USER_INVITATION_RESENT = "user.invitation_resent"
    USER_ACTIVATED = "user.activated"
    USER_DEACTIVATED = "user.deactivated"
    USER_UNLOCKED = "user.unlocked"
    USER_SESSIONS_REVOKED = "user.sessions_revoked"
    USER_ORGANIZATION_ASSIGNED = "user.organization_assigned"

    # --- ECA <-> Association links ---
    LINK_REQUESTED = "link.requested"
    LINK_ACCEPTED = "link.accepted"
    LINK_REJECTED = "link.rejected"
    LINK_REMOVED = "link.removed"

    # --- Weighings ---
    WEIGHING_CREATED = "weighing.created"
    WEIGHING_VALIDATED = "weighing.validated"
    WEIGHING_REJECTED = "weighing.rejected"
    WEIGHING_PAID = "weighing.paid"
    WEIGHINGS_EXPORTED = "weighing.exported"

    # --- Transactions ---
    TRANSACTION_CREATED = "transaction.created"
    TRANSACTION_CANCELLED = "transaction.cancelled"
    TRANSACTION_DELIVERED = "transaction.delivered"
    TRANSACTION_PAID = "transaction.paid"
    TRANSACTIONS_EXPORTED = "transaction.exported"

    # --- Inventory ---
    INVENTORY_UPDATED = "inventory.updated"
    INVENTORY_EXPORTED = "inventory.exported"
    WAREHOUSE_CREATED = "warehouse.created"
    WAREHOUSE_ORGANIZATION_ASSIGNED = "warehouse.organization_assigned"
    WAREHOUSE_UPDATED = "warehouse.updated"

    # --- Catalogs (backoffice) ---
    CATALOG_CREATED = "catalog.created"
    CATALOG_UPDATED = "catalog.updated"


SUCCESS = "success"
FAILURE = "failure"
