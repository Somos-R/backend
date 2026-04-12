import enum


class UserType(str, enum.Enum):
    citizen = "citizen"
    building_admin = "building_admin"
    recycler = "recycler"
    eca_operator = "eca_operator"
    asobeum_admin = "asobeum_admin"
    b2b_client = "b2b_client"


class VerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
