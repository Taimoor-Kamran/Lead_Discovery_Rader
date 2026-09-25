"""The subset of the Places API (New) response this build relies on.

Two levels on purpose: a lenient page model, so one bad place does not throw away the
other nineteen, and a strict `Place` used by `validate()` on a single record.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PLACE_URL_TEMPLATE = "https://www.google.com/maps/place/?q=place_id:{place_id}"


class PlacesModel(BaseModel):
    # The API is camelCase and adds fields over time; unknown ones are ignored, not fatal.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class LatLng(PlacesModel):
    latitude: float | None = None
    longitude: float | None = None


class LocalizedText(PlacesModel):
    text: str | None = None
    language_code: str | None = Field(default=None, alias="languageCode")


class AddressComponent(PlacesModel):
    long_text: str | None = Field(default=None, alias="longText")
    short_text: str | None = Field(default=None, alias="shortText")
    types: list[str] = Field(default_factory=list)


class Place(PlacesModel):
    """One business. Only `id` is required — everything else may legitimately be absent."""

    id: str = Field(min_length=1)
    display_name: LocalizedText | None = Field(default=None, alias="displayName")
    formatted_address: str | None = Field(default=None, alias="formattedAddress")
    address_components: list[AddressComponent] | None = Field(
        default=None, alias="addressComponents"
    )
    location: LatLng | None = None
    national_phone_number: str | None = Field(default=None, alias="nationalPhoneNumber")
    international_phone_number: str | None = Field(default=None, alias="internationalPhoneNumber")
    website_uri: str | None = Field(default=None, alias="websiteUri")
    business_status: str | None = Field(default=None, alias="businessStatus")
    types: list[str] | None = None
    primary_type: str | None = Field(default=None, alias="primaryType")
    rating: float | None = None
    user_rating_count: int | None = Field(default=None, alias="userRatingCount")

    @property
    def source_url(self) -> str:
        return PLACE_URL_TEMPLATE.format(place_id=self.id)


class TextSearchPage(PlacesModel):
    """One page of results. Places stay as dicts so each can be validated on its own."""

    places: list[dict[str, Any]] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, alias="nextPageToken")
