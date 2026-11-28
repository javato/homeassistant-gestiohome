"""Constants definition."""

from __future__ import annotations

DOMAIN = "gestiohome"

CONF_URL = "url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# full "Código APP Móvil" of the dwelling (unique ID of the entry)
CONF_CODE = "code"
# shortest suffix of the code (4+ chars, lower case): the integration ID used in
# entity unique IDs and statistic IDs, like the "scups" of edata
CONF_SCODE = "scode"

# options: the meters the user chose, keyed by GestioHome's numeric meter ID,
# which is the only thing that tells whether a meter is the same one:
# {meter id: {"name": name the user gave it, "stat": "<name>_<number>", the
# part of the statistic ID fixed when the meter was added}}
CONF_METERS = "meters"
# options: every meter ID GestioHome showed when the meters were last chosen,
# so only meters that appear later are reported as new
CONF_KNOWN = "known"
# meter config keys
CONF_NAME = "name"
CONF_STAT = "stat"
