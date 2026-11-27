# GestioHome para Home Assistant

Integración para Home Assistant que lee los consumos comunitarios de **GestioHome** (Usanca Metering Solutions) y los lleva al **Panel de Energía con su fecha real**:

| Suministro | Unidad | En el Panel de Energía |
|---|---|---|
| ACS (agua caliente sanitaria) | m³ | Agua |
| Calor (calefacción, suelo radiante) | kWh | Gas (energía térmica) |
| Frío (refrigeración) | kWh | Gas (energía térmica) |

Sigue la misma estrategia que [e-data](https://github.com/uvejota/homeassistant-edata) para la luz: en lugar de un sensor que Home Assistant registra cuando lo lee, importa **estadísticas externas** con la fecha y hora de cada consumo.

## Por qué no un sensor normal

GestioHome publica las lecturas con retraso (una al día, a horas variables). Un sensor normal haría que Home Assistant apuntase cada consumo a la hora en que se leyó la web, no a cuando ocurrió, y el acumulado mensual de la web se pone a cero cada mes. Esta integración:

- Lee la **lectura absoluta del contador** con su fecha y hora exactas, que nunca se pone a cero.
- Escribe **una fila por día** (a las 00:00) en `gestiohome:<id>_acs`, `gestiohome:<id>_calor` y `gestiohome:<id>_frio`, con un total que sigue sumando de un mes a otro.
- Cada día se lleva lo que contó el contador desde la lectura anterior, **igual que la web**: los totales diarios y mensuales cuadran con GestioHome.
- La primera vez importa **todo el histórico** que tenga GestioHome.

A diferencia de Datadis (luz), GestioHome no da consumo por horas, solo una lectura al día. Por eso las estadísticas son diarias: en la vista por horas del Panel de Energía verás todo el consumo del día a medianoche; usa las vistas por día, semana o mes.

## Instalación

Desde el **Marketplace** de Home Assistant (antes HACS):

1. Ajustes → Marketplace → menú ⋮ → *Repositorios personalizados*.
2. Añade `https://github.com/javato/homeassistant-gestiohome` como *Integración*.
3. Busca **GestioHome**, instálala y reinicia Home Assistant.

## Configuración

Ajustes → Dispositivos y servicios → Añadir integración → **GestioHome**:

- **Dirección de GestioHome**: la de tu edificio, por ejemplo `https://miedificio.gestiohome.com` (vale pegar la dirección de la página de inicio de sesión).
- **Usuario** y **contraseña** de la web de GestioHome.

La integración encuentra sola la vivienda y sus contadores. Si GestioHome deja de aceptar la contraseña, Home Assistant pedirá la nueva.

### Panel de Energía

Ajustes → Paneles → Energía:

- **Agua**: añade `gestiohome:<id>_acs`.
- **Gas**: añade `gestiohome:<id>_calor` (y `gestiohome:<id>_frio` si lo usas).

Así quedan separados del consumo eléctrico. El `<id>` son los últimos caracteres del código de la app de tu vivienda; aparece en el sensor *Información del suministro*.

## Entidades

| Entidad | Qué muestra |
|---|---|
| Información del suministro | Hora de la última lectura, vivienda y estadísticas creadas |
| Lectura contador (ACS, calor, frío) | Última lectura absoluta del contador |
| Último consumo registrado | Consumo del último día con lectura |
| Consumo en el mes / durante último mes | Los mismos totales que muestra la web |
| Reparar estadísticas de Home Assistant (botón) | Borra las estadísticas de la integración y las vuelve a importar enteras |
| Forzar sincronización (botón) | Vuelve a consultar GestioHome ahora, también los contadores |

Los sensores no tienen *state class* a propósito: son solo para ver los datos y no generan estadísticas con la hora de lectura. Para gráficas usa las estadísticas `gestiohome:...`.

## Cómo funciona por dentro

- Inicia sesión en la web igual que el navegador y usa su propio servicio de datos (`getStatisticData.php`).
- Consulta cada hora, pero si ya tiene una lectura de menos de 6 horas no vuelve a preguntar.
- Cada sincronización vuelve a escribir los últimos 31 días: si una lectura llega tarde a GestioHome, también la de un día pasado, se coloca en su día real.
- Si el contador baja (cambio o puesta a cero), no resta nada y toma el nuevo valor como punto de partida.

## Licencia

MIT
