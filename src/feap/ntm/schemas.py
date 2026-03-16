"""Pydantic schemas for NTM verification (dual matching via LLM)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NtmVerifyRequest(BaseModel):
    """Request to verify NTM measures against product description."""

    code: str = Field(description="10-digit TN VED code")
    product_description: str = Field(
        min_length=3,
        description="Product description for dual matching",
    )


class NtmVerdict(BaseModel):
    """LLM verdict for a single NTM measure."""

    measure_index: int = Field(description="Index of the measure in the input list")
    status: str = Field(description="applies | does_not_apply | uncertain")
    reasoning: str = Field(description="Brief justification")


class NtmVerificationLLMResponse(BaseModel):
    """LLM output: verdicts for all measures."""

    verdicts: list[NtmVerdict] = Field(default_factory=list)


class NtmVerifiedMeasure(BaseModel):
    """A single NTM measure with verification result."""

    measure_type: str
    measure_type_kind: int
    name: str
    status: str = Field(description="applies | does_not_apply | uncertain | may_apply")
    qualifier: str | None = None
    legal_basis: str | None = None
    note: str | None = None
    scope: str = "eaeu"
    valid_to: str | None = None
    reasoning: str | None = Field(
        default=None,
        description="LLM reasoning for the verdict (only when verified)",
    )


class NtmVerifyResponse(BaseModel):
    """Response from NTM verification endpoint."""

    code: str
    code_formatted: str | None = None
    name: str | None = None
    product_description: str
    measures: list[NtmVerifiedMeasure] = Field(default_factory=list)
    total_count: int = 0
    verified: bool = Field(
        default=False,
        description="True if LLM verification was performed",
    )
    has_prohibitions: bool = False
    has_licensing: bool = False
    has_technical_regulations: bool = False
    has_sanitary_control: bool = False
    has_veterinary_control: bool = False
    has_phytosanitary_control: bool = False
    has_marking: bool = False
