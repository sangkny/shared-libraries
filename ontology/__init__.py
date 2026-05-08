# shared-libraries/ontology/__init__.py
from .base import (
    OntologyDomain, ValidatorType, Severity,
    ValidationError, ValidationResult,
)
from .validator import (
    OntologyValidator,
    SemanticValidator, StructuralValidator,
    ConstraintValidator, DependencyValidator,
)

__all__ = [
    "OntologyDomain", "ValidatorType", "Severity",
    "ValidationError", "ValidationResult",
    "OntologyValidator",
    "SemanticValidator", "StructuralValidator",
    "ConstraintValidator", "DependencyValidator",
]
