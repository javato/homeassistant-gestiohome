"""Constants definition."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

DOMAIN = "gestiohome"

CONF_URL = "url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# full "Código APP Móvil" of the dwelling (unique ID of the entry)
CONF_CODE = "code"
# shortest suffix of the code (4+ chars, lower case): the integration ID used in
# entity unique IDs and statistic IDs, like the "scups" of edata
CONF_SCODE = "scode"

# meter of each supply found when set up: {supply key: [meter id, meter name]}
CONF_METERS = "meters"


@dataclass(frozen=True, slots=True)
class Supply:
    """A kind of supply GestioHome serves, one page each."""

    key: str
    page: str
    unit: str
    unit_class: str


SUPPLIES: tuple[Supply, ...] = (
    Supply("acs", "iuACS.php", UnitOfVolume.CUBIC_METERS, SensorDeviceClass.VOLUME),
    Supply(
        "calor", "iuCalor.php", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY
    ),
    Supply("frio", "iuFrio.php", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
)
