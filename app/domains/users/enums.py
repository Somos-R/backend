import enum


class UserType(str, enum.Enum):
    citizen = "citizen"
    building_admin = "building_admin"
    recycler = "recycler"
    eca_operator = "eca_operator"
    asobeum_admin = "asobeum_admin"
    b2b_client = "b2b_client"


class DocumentType(str, enum.Enum):
    CC = "CC"      # Cédula de Ciudadanía
    CE = "CE"      # Cédula de Extranjería
    NIT = "NIT"    # Número de Identificación Tributaria
    PA = "PA"      # Pasaporte
    PPT = "PPT"    # Permiso de Protección Temporal


class VerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
