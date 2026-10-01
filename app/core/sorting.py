"""Ordering shared by the list endpoints that accept `sort` and `order`."""
import enum


class SortOrder(str, enum.Enum):
    asc = "asc"
    desc = "desc"
