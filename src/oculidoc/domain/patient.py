"""Patient domain model."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class Sex(StrEnum):
    """Patient sex used for records and personalized questions."""

    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class ClinicalDiagnosis(StrEnum):
    """Standardized disorders-of-consciousness diagnosis."""

    UNKNOWN = "unknown"
    COMA = "coma"
    UWS = "uws"
    MCS_MINUS = "mcs_minus"
    MCS_PLUS = "mcs_plus"
    EMCS = "emcs"
    OTHER = "other"


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def _clean_optional_text(value: str | None) -> str | None:
    """Trim optional text and convert empty strings to None."""
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


@dataclass(slots=True)
class Patient:
    """Core patient information independent of storage and user interface."""

    patient_code: str
    family_name: str

    sex: Sex = Sex.UNKNOWN
    date_of_birth: date | None = None
    etiology: str | None = None
    clinical_diagnosis: str | None = None
    enrollment_date: date = field(default_factory=date.today)
    notes: str = ""

    patient_id: UUID = field(default_factory=uuid4)
    is_active: bool = True
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        """Normalize fields and enforce basic domain rules."""
        self.patient_code = self.patient_code.strip()
        self.family_name = self.family_name.strip()
        self.etiology = _clean_optional_text(self.etiology)
        self.clinical_diagnosis = _clean_optional_text(self.clinical_diagnosis)
        self.notes = self.notes.strip()

        if not self.patient_code:
            raise ValueError("Patient code must not be empty.")

        if not self.family_name:
            raise ValueError("Family name must not be empty.")

        if self.date_of_birth is not None and self.date_of_birth > self.enrollment_date:
            raise ValueError("Date of birth must not be later than enrollment date.")

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware.")

    @property
    def display_label(self) -> str:
        """Return a concise label for the administrator interface."""
        return f"{self.family_name}患者（{self.patient_code}）"

    def age_on(self, reference_date: date) -> int | None:
        """Calculate age on a specific date."""
        if self.date_of_birth is None:
            return None

        if reference_date < self.date_of_birth:
            raise ValueError("Reference date must not be earlier than date of birth.")

        birthday_has_occurred = (
            reference_date.month,
            reference_date.day,
        ) >= (
            self.date_of_birth.month,
            self.date_of_birth.day,
        )

        return reference_date.year - self.date_of_birth.year - int(not birthday_has_occurred)

    def deactivate(self) -> None:
        """Deactivate the patient without deleting historical records."""
        self.is_active = False
        self.updated_at = _utc_now()

    def activate(self) -> None:
        """Reactivate a previously deactivated patient."""
        self.is_active = True
        self.updated_at = _utc_now()
