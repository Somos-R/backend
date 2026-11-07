import enum


class OrganizationType(str, enum.Enum):
    association = "association"
    eca = "eca"


class LinkStatus(str, enum.Enum):
    """A link between an ECA and an Association. The ECA asks; the Association decides."""

    requested = "requested"
    active = "active"
    rejected = "rejected"
    removed = "removed"  # withdrawn by either side (or the ECA cancelled its request)


class Weekday(str, enum.Enum):
    """A day of an ECA's reception hours, in calendar order (the order the hours are listed in)."""

    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"


class OrganizationStatus(str, enum.Enum):
    """Where an organization is in its onboarding. Only `approved` organizations operate."""

    draft = "draft"  # being filled in; nothing sent yet
    submitted = "submitted"  # sent, waiting for a reviewer
    in_review = "in_review"  # a reviewer has it
    changes_requested = "changes_requested"  # the applicant must correct and resend
    approved = "approved"
    rejected = "rejected"  # final
    suspended = "suspended"  # reserved: revalidation is not defined yet
