"""Password rules (spec v0.8.0 §5): length, not the email, not a well-known password.

Kept deliberately small and boring. The list below is the handful of passwords that
appear at the top of every breach corpus; a long random passphrase is what the admin's
temporary password generator hands out, and what these rules push a user towards.
"""

from app.core.errors import ValidationFailedError
from app.modules.auth.schemas import MIN_PASSWORD_LENGTH

COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "passwordpassword",
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "12345678901",
        "123456789012",
        "1234567890123",
        "qwerty",
        "qwertyuiop",
        "qwertyuiop12",
        "qwertyuiopasdf",
        "qwerty123456",
        "letmein",
        "letmein12345",
        "welcome",
        "welcome12345",
        "welcome123456",
        "admin",
        "admin1234567",
        "administrator",
        "changeme",
        "changeme1234",
        "changemenow1",
        "iloveyou",
        "iloveyou1234",
        "trustno1",
        "abc123456789",
        "monkey123456",
        "dragon123456",
        "sunshine1234",
        "football1234",
        "baseball1234",
        "princess1234",
        "superman1234",
        "temporary123",
        "temppassword",
        "radar1234567",
        "leadradar123",
    }
)


def password_problems(password: str, *, email: str | None = None) -> list[str]:
    """Every rule the password breaks, in plain words. Empty means it is acceptable."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"it must be at least {MIN_PASSWORD_LENGTH} characters long")
    lowered = password.strip().lower()
    if email:
        address = email.strip().lower()
        local_part = address.split("@", 1)[0]
        if lowered == address or (len(local_part) >= 6 and lowered == local_part):
            problems.append("it must not be your email address")
    if lowered in COMMON_PASSWORDS:
        problems.append("it is one of the most common passwords and is not allowed")
    return problems


def validate_new_password(password: str, *, email: str | None = None) -> None:
    """Raise `ValidationFailedError` listing every broken rule. Never echoes the password."""
    problems = password_problems(password, email=email)
    if problems:
        raise ValidationFailedError(
            "That password cannot be used: " + "; ".join(problems),
            details={"rules": problems, "minimum_length": MIN_PASSWORD_LENGTH},
            code="weak_password",
        )
