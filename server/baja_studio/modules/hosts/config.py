"""The hosts module's settings: seats, card layout, canned messages and quick replies.

Seats, not users (WP9 brief §2): four fixed seats with a typed screen name
and a colour, no accounts. Which microphone and in-ear bus belong to a seat
is an audio fact, set in the audio settings (WP9b §2); WP9's
`audio_channel` is dropped from saved seats (`settings_version` 2).

`layout` is production's choice of card order and visibility; an empty list
means "every card by priority". The pushed card is not here: it is a
moment in the show, so it lives in state only.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from baja_studio.core.settings import ModuleSettings

SEAT_IDS = (1, 2, 3, 4)
COLOUR_PATTERN = r"^#[0-9a-fA-F]{6}$"
MAX_MESSAGE_LENGTH = 200
MAX_QUICK_REPLY_LENGTH = 40
# The reactions every hosts screen offers; quick replies are configured on top.
REACTIONS = ("👍", "👎", "✋", "❓")


class StrictModel(BaseModel):
    """Unknown keys are errors, so a typo in a saved document is caught."""

    model_config = ConfigDict(extra="forbid")


class SeatSettings(StrictModel):
    """One seat at the hosts' desk."""

    id: int = Field(ge=1, le=4)
    screen_name: str = Field(default="", max_length=24, description="The name messages show.")
    color: str = Field(default="#5b7fff", pattern=COLOUR_PATTERN)
    enabled: bool = Field(default=True, description="Off: production cannot address this seat.")

    @model_validator(mode="before")
    @classmethod
    def _forget_the_wp9_audio_channel(cls, values: Any) -> Any:
        # WP9 kept the seat's microphone here; it moved to the audio settings.
        # Saved seats and older exported files still carry it: drop it.
        if isinstance(values, dict):
            return {key: value for key, value in values.items() if key != "audio_channel"}
        return values


class CardLayoutEntry(StrictModel):
    """Where one card goes on the hosts screen and whether it shows."""

    id: str = Field(min_length=1, max_length=64)
    visible: bool = True


def default_seats() -> list[SeatSettings]:
    """Two hosts and two guests, in the audio page's channel colours (channels 1-4)."""
    return [
        SeatSettings(id=1, screen_name="Host 1", color="#e74c3c"),
        SeatSettings(id=2, screen_name="Host 2", color="#e67e22"),
        SeatSettings(id=3, screen_name="Guest 1", color="#f1c40f", enabled=False),
        SeatSettings(id=4, screen_name="Guest 2", color="#2ecc71", enabled=False),
    ]


DEFAULT_CANNED_MESSAGES = [
    "30 s",
    "wrap up",
    "go to break",
    "throw to replay",
    "mention sponsor",
]
DEFAULT_QUICK_REPLIES = ["ok", "need 1 min", "repeat?"]
# What a host can send production with one tap (WP9b §1), in the hosts' language.
DEFAULT_HOST_PHRASES = ["preciso de 1 min", "problema no áudio", "repete?", "pronto"]


class HostsSettings(ModuleSettings):
    """Settings of the hosts module."""

    # 2: seats no longer carry `audio_channel` (it moved to the audio settings).
    settings_version = 2

    seats: list[SeatSettings] = Field(default_factory=default_seats)
    layout: list[CardLayoutEntry] = Field(
        default_factory=list,
        description="Card order and visibility on the hosts screen; empty: by priority.",
    )
    canned_messages: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CANNED_MESSAGES),
        description="One-tap messages on the production side.",
    )
    quick_replies: list[str] = Field(
        default_factory=lambda: list(DEFAULT_QUICK_REPLIES),
        description="Answers a host can tap besides the reactions.",
    )
    message_expires_s: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="Seconds after which a message fades if nobody answered; 0: never.",
    )
    chime: bool = Field(
        default=False,
        description="A soft sound on the hosts screen for a new message (hosts wear IEMs).",
    )
    chat_size: Literal["small", "medium", "large"] = Field(
        default="small",
        description="How much of the hosts screen the chat takes when nothing is new.",
    )
    host_phrases: list[str] = Field(
        default_factory=lambda: list(DEFAULT_HOST_PHRASES),
        description="One-tap messages a host can send to production.",
    )
    production_chime: bool = Field(
        default=True,
        description="A sound on the Hosts page when a host sends a message.",
    )

    @field_validator("seats")
    @classmethod
    def _one_of_each_seat(cls, seats: list[SeatSettings]) -> list[SeatSettings]:
        if sorted(seat.id for seat in seats) != list(SEAT_IDS):
            raise ValueError("there must be exactly the seats 1, 2, 3 and 4, each once")
        return sorted(seats, key=lambda seat: seat.id)

    @field_validator("canned_messages")
    @classmethod
    def _canned_fit_a_message(cls, messages: list[str]) -> list[str]:
        return _non_blank(messages, MAX_MESSAGE_LENGTH, "canned message")

    @field_validator("host_phrases")
    @classmethod
    def _phrases_fit_a_message(cls, phrases: list[str]) -> list[str]:
        return _non_blank(phrases, MAX_MESSAGE_LENGTH, "host phrase")

    @field_validator("quick_replies")
    @classmethod
    def _replies_are_short(cls, replies: list[str]) -> list[str]:
        return _non_blank(replies, MAX_QUICK_REPLY_LENGTH, "quick reply")

    def seat(self, seat_id: int) -> SeatSettings | None:
        """The seat with this id, or None."""
        return next((seat for seat in self.seats if seat.id == seat_id), None)


def _non_blank(texts: list[str], limit: int, what: str) -> list[str]:
    cleaned = [text.strip() for text in texts]
    for index, text in enumerate(cleaned):
        if not text:
            raise ValueError(f"{what} {index + 1} is empty")
        if len(text) > limit:
            raise ValueError(f"{what} {index + 1} is longer than {limit} characters")
    return cleaned
