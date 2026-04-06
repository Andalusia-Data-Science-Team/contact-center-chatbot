# db/__init__.py
from .database import (
    query_availability,
    query_availability_with_fallback,
    query_doctor_slots,
    query_doctor_slots_with_fallback,
    aggregate_doctor_slots,
)
