from __future__ import annotations

import logging

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SaveFamilyConfigEntry
from .core.protocol import PhotoWallItem, SaveFamilyError, SaveFamilyWatch
from .entity import SaveFamilySettingsEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SaveFamilyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    settings_coordinator = entry.runtime_data.settings_coordinator
    entities_by_did: dict[str, SaveFamilyLatestPhotoImage] = {}

    @callback
    def _async_handle_update() -> None:
        new_entities = []
        for did, state in coordinator.data.items():
            if did not in entities_by_did:
                entity = SaveFamilyLatestPhotoImage(settings_coordinator, state.watch, "latest_photo")
                entities_by_did[did] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_handle_update))
    _async_handle_update()


class SaveFamilyLatestPhotoImage(SaveFamilySettingsEntity, ImageEntity):
    """Most recent photo of the watch's photo wall (remote captures and uploads)."""

    _attr_translation_key = "latest_photo"
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator, watch: SaveFamilyWatch, key: str) -> None:
        SaveFamilySettingsEntity.__init__(self, coordinator, watch, key)
        ImageEntity.__init__(self, coordinator.hass)
        self._photo_id: int | None = None
        self._image: bytes | None = None
        self._update_photo()

    def _latest_photo(self) -> PhotoWallItem | None:
        watch_settings = self.watch_settings
        return watch_settings.latest_photo if watch_settings else None

    def _update_photo(self) -> None:
        photo = self._latest_photo()
        if photo is None or photo.id == self._photo_id:
            return
        self._photo_id = photo.id
        self._image = None
        self._attr_image_last_updated = photo.created or dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_photo()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        photo = self._latest_photo()
        return {"filename": photo.filename} if photo else {}

    async def async_image(self) -> bytes | None:
        photo = self._latest_photo()
        if photo is None:
            return None
        if self._image is None or photo.id != self._photo_id:
            try:
                self._image = await self.coordinator.client.async_download_photo(self._did, photo.filename)
            except SaveFamilyError as exc:
                _LOGGER.debug("v2_downloadPictrueDoor failed for %s: %s", self._did, exc)
                return None
            self._photo_id = photo.id
        return self._image
