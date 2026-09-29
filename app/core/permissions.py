"""Role catalog and the role groups that gate each endpoint.

The source of truth for *who may do what* is docs/matriz-permisos.md; this
module is that matrix expressed as data. Keep both in sync.
"""
from fastapi import HTTPException, status

# --- Roles (codes must fit roles.code, String(20)) ---------------------------
ECA_ADMIN = "eca_admin"                  # ECA · Administrativo
ECA_OPERATOR = "eca_operator"            # ECA · Operador de báscula
ECA_WAREHOUSE = "eca_warehouse"          # ECA · Encargado de bodega
ASSOC_ADMIN = "association_admin"        # Asociación · Administrativo
ASSOC_OPERATOR = "association_operator"  # Asociación · Operativo
ROUTE_MANAGER = "route_manager"          # Asociación · Encargado de rutas

# A role is only valid for one actor type.
ROLE_USER_TYPE: dict[str, str] = {
    ECA_ADMIN: "eca",
    ECA_OPERATOR: "eca",
    ECA_WAREHOUSE: "eca",
    ASSOC_ADMIN: "association",
    ASSOC_OPERATOR: "association",
    ROUTE_MANAGER: "association",
}

ORG_ADMINS = frozenset({ECA_ADMIN, ASSOC_ADMIN})

# --- Role groups per capability ---------------------------------------------
USERS_DIRECTORY = frozenset(ROLE_USER_TYPE)  # any staff role can look up recyclers
VERIFY_RECYCLERS = frozenset({ASSOC_ADMIN, ASSOC_OPERATOR})

WEIGHINGS_READ = frozenset({ECA_ADMIN, ECA_OPERATOR, ECA_WAREHOUSE, ASSOC_ADMIN, ASSOC_OPERATOR})
WEIGHINGS_CREATE = frozenset({ECA_ADMIN, ECA_OPERATOR})
WEIGHINGS_REVIEW = frozenset({ECA_ADMIN, ECA_OPERATOR, ASSOC_ADMIN, ASSOC_OPERATOR})

INVENTORY_READ = frozenset({ECA_ADMIN, ECA_OPERATOR, ECA_WAREHOUSE, ASSOC_ADMIN, ASSOC_OPERATOR})
INVENTORY_WRITE = frozenset({ECA_ADMIN, ECA_WAREHOUSE})

TRANSACTIONS_READ = frozenset({ECA_ADMIN, ECA_OPERATOR, ECA_WAREHOUSE, ASSOC_ADMIN})
TRANSACTIONS_WRITE = frozenset({ECA_ADMIN, ECA_WAREHOUSE})

PAYMENTS = frozenset({ECA_ADMIN, ASSOC_ADMIN})

# Profile fields that only an organization admin may change (never self-service).
PRIVILEGED_USER_FIELDS = frozenset({"role_code", "permissions", "association_id", "employee_code"})

FORBIDDEN = "No tienes permisos para realizar esta acción"


def forbidden(detail: str = FORBIDDEN) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def has_role(user, roles: frozenset[str]) -> bool:
    """True when the user holds one of `roles` *and* that role fits their actor type."""
    role = user.role_code
    return role in roles and ROLE_USER_TYPE.get(role) == user.user_type_code


def ensure_role(user, roles: frozenset[str]) -> None:
    if not has_role(user, roles):
        raise forbidden()


def visible_user_types(user) -> frozenset[str]:
    """Actor types whose profiles this staff user may look up."""
    if has_role(user, frozenset({ASSOC_ADMIN})):
        return frozenset({"citizen", "building", "recycler", "eca", "association", "b2b_client"})
    if has_role(user, frozenset({ECA_ADMIN})):
        return frozenset({"eca", "recycler"})
    return frozenset({"recycler"})


def manageable_user_types(user) -> frozenset[str]:
    """Actor types whose profiles this org admin may edit."""
    if has_role(user, frozenset({ASSOC_ADMIN})):
        return frozenset({"association", "recycler"})
    if has_role(user, frozenset({ECA_ADMIN})):
        return frozenset({"eca"})
    return frozenset()


def ensure_can_assign_role(actor, role_code: str, target_user_type: str) -> None:
    """Only an org admin may hand out roles, and only those of their own organization."""
    if actor is None or not has_role(actor, ORG_ADMINS):
        raise forbidden("Solo un administrador puede asignar roles")
    role_type = ROLE_USER_TYPE.get(role_code)
    if role_type is None or role_type != target_user_type:
        raise HTTPException(
            status_code=422,
            detail=f"role_code '{role_code}' no es válido para un usuario de tipo '{target_user_type}'",
        )
    if role_type != actor.user_type_code:
        raise forbidden("Solo puedes asignar roles de tu propia organización")
