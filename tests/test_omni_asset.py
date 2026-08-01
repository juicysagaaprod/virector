import pytest
from pydantic import ValidationError

from virector.models.omni_asset import (
    AssetRole,
    BindingModality,
    OmniAsset,
    OmniMediaType,
    ReferenceBinding,
    ReferenceOperation,
)


def test_omni_asset_requires_matching_media_tag() -> None:
    with pytest.raises(ValidationError, match="must match its media type"):
        OmniAsset(
            index=1,
            tag="@video1",
            media_type=OmniMediaType.image,
            description="Lead character",
            roles=[AssetRole.character_identity],
        )


def test_reference_binding_rejects_visible_voice_asset() -> None:
    with pytest.raises(ValidationError, match="Only visual"):
        ReferenceBinding(
            asset_tags=["@audio1"],
            modality=BindingModality.voice,
            operations=[ReferenceOperation.reference],
            controls=[AssetRole.voice],
            target="Lead voice",
            instruction="Use the referenced voice.",
            visible=True,
        )
