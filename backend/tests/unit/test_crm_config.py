"""CRM settings: the fake destination is for development only; the delay follows the undo window."""

import pytest
from pydantic import SecretStr

from app.core.config import Settings


@pytest.mark.parametrize("environment", ["local", "development", "ci"])
def test_the_fake_destination_is_allowed_in_development_environments(environment: str) -> None:
    settings = Settings(environment=environment, crm_destination="fake")  # type: ignore[arg-type]
    assert settings.crm_destination == "fake"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_fake_destination_is_refused_elsewhere(environment: str) -> None:
    with pytest.raises(ValueError, match="CRM_DESTINATION=fake"):
        Settings(
            environment=environment,  # type: ignore[arg-type]
            crm_destination="fake",
            jwt_secret=SecretStr("x" * 40),
        )


def test_csv_is_the_default_and_airtable_is_allowed_anywhere() -> None:
    # The test environment sets CRM_DESTINATION=fake, so the default is read off the model.
    assert Settings.model_fields["crm_destination"].default == "csv"
    assert (
        Settings(
            environment="production", jwt_secret=SecretStr("x" * 40), crm_destination="csv"
        ).crm_destination
        == "csv"
    )
    assert (
        Settings(
            environment="production", jwt_secret=SecretStr("x" * 40), crm_destination="airtable"
        ).crm_destination
        == "airtable"
    )


def test_the_sync_delay_defaults_to_the_undo_window() -> None:
    assert Settings(review_undo_window_minutes=45).resolved_crm_sync_delay_minutes == 45
    assert Settings(crm_sync_delay_minutes=5).resolved_crm_sync_delay_minutes == 5
    assert Settings(crm_sync_delay_minutes="").resolved_crm_sync_delay_minutes == 30  # type: ignore[arg-type]
