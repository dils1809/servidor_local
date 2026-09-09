# Proyecto 1 — Uso de un protocolo existente

**Model Context Protocol sobre JSON-RPC 2.0**

Dilary Cruz · CC3067 Redes · Universidad del Valle de Guatemala

Repositorio: `https://github.com/dils1809/servidor_local`
Servidor remoto: `https://vibbo-mcp.onrender.com`

---

## Contenido

- [1. Qué se construyó](#1-qué-se-construyó)
- [2. Especificación de los servidores MCP](#2-especificación-de-los-servidores-mcp)
- [3. Clasificación de los mensajes JSON-RPC capturados](#3-clasificación-de-los-mensajes-json-rpc-capturados)
- [4. Análisis por capas](#4-análisis-por-capas)
- [5. Dificultades y cómo se resolvieron](#5-dificultades-y-cómo-se-resolvieron)
- [6. Conclusiones](#6-conclusiones)

---

## 1. Qué se construyó

El proyecto tiene dos mitades, y el protocolo está implementado a mano en las
dos.

**El servidor MCP** (`src/`) atiende el soporte al cliente de VIBBO, una
tienda real de tés funcionales que opera sobre Shopify. Expone tres
herramientas, cuatro documentos de referencia y una plantilla de escalamiento.
No usa el SDK de MCP, FastMCP ni ninguna librería de protocolo: el framing, el
parseo, la validación y la máquina de estados del ciclo de vida están escritos
en el repositorio.

**El chatbot** (`chatbot/`) es el *anfitrión*. Corre cuatro servidores MCP a la
vez y le presenta al modelo las herramientas de todos como una sola lista:

| Servidor | Transporte | Origen |
| --- | --- | --- |
| `vibbo` | stdio | propio, `src/` |
| `filesystem` | stdio | oficial de Anthropic |
| `git` | stdio | oficial de Anthropic |
| `vibbo-remote` | HTTP | propio, desplegado en Render |

El cliente MCP también está escrito a mano, y reutiliza el mismo
`src/jsonrpc.py` que usa el servidor. Hay **una sola definición del formato de
mensaje** en el repositorio, usada desde los dos extremos.

### El dato que resume el proyecto

La misma herramienta, invocada por los dos transportes, devuelve bytes
idénticos:

```
stdio  vibbo          In transit  USPS  9400366674684573766792
HTTP   vibbo-remote   In transit  USPS  9400366674684573766792
```

Cambiar de stdio a HTTP no tocó una sola línea de `src/handlers/`, `src/db.py`,
`src/mcp_server.py` ni `src/jsonrpc.py`. Se agregó `src/http_transport.py` y
una bandera `--http`. Esa es la separación entre lógica de negocio y
transporte, medida en diff.

---

## 2. Especificación de los servidores MCP

### 2.1 Versión y transportes

| | |
| --- | --- |
| Revisión MCP | `2025-11-25` |
| Versiones aceptadas en negociación | `2025-11-25`, `2025-06-18`, `2025-03-26` |
| Protocolo base | JSON-RPC 2.0 |
| Transporte local | stdio, framing NDJSON (un mensaje por línea, delimitador `\n`) |
| Transporte remoto | Streamable HTTP (un mensaje por cuerpo de petición) |

No se usa framing por longitud (`Content-Length`) en stdio. El batching de
JSON-RPC fue eliminado de MCP en la revisión `2025-11-25`, así que un arreglo
JSON se rechaza con `-32600`.

### 2.2 Métodos implementados

| Método | Tipo | Propósito |
| --- | --- | --- |
| `initialize` | solicitud | Negocia versión y capacidades |
| `notifications/initialized` | notificación | El cliente confirma que está listo |
| `ping` | solicitud | Verificación de vida, resultado vacío |
| `tools/list` | solicitud | Descubrimiento de herramientas |
| `tools/call` | solicitud | Invocación de una herramienta |
| `resources/list` | solicitud | Descubrimiento de documentos |
| `resources/read` | solicitud | Lectura de un documento |
| `prompts/list` | solicitud | Descubrimiento de plantillas |
| `prompts/get` | solicitud | Renderizado de una plantilla |

### 2.3 Ciclo de vida

```
UNINITIALIZED ──initialize──> INITIALIZING ──notifications/initialized──> READY
```

Antes de completar el handshake solo se aceptan `initialize` y `ping`.
Cualquier otro método recibe `-32600`.

Las capacidades **se derivan, no se declaran**. `MCPServer` arranca con el
conjunto vacío y `register_feature()` agrega una entrada por cada función
realmente registrada. El servidor no puede anunciar algo que no implementa,
porque el anuncio y la tabla de enrutamiento se llenan con la misma llamada.

### 2.4 Endpoints del servidor remoto

Base: `https://vibbo-mcp.onrender.com`

| Método HTTP | Ruta | Propósito | Respuesta |
| --- | --- | --- | --- |
| `GET` | `/` o `/health` | Vida del servicio y sesiones abiertas | `200`, JSON |
| `POST` | `/mcp` | Un mensaje JSON-RPC por petición | `200` con cuerpo, o `202` vacío |
| `DELETE` | `/mcp` | Cierra la sesión indicada en el encabezado | `204` sin contenido |

**Manejo de sesión.** HTTP no tiene estado y el ciclo de vida de MCP sí. Un
`POST /mcp` que lleve `initialize` abre una sesión y la respuesta devuelve su
identificador en el encabezado `Mcp-Session-Id`. Toda petición posterior debe
repetir ese encabezado; sin él el servidor responde `404` con un error
JSON-RPC `-32600`, porque sin sesión no hay estado de ciclo de vida que
honrar.

Internamente hay **un `MCPServer` por sesión**, en un registro con barrido de
sesiones inactivas a los 30 minutos. Sobre stdio esto no existe: un proceso es
un cliente y el estado es implícito.

### 2.5 Herramientas

#### `get_order_status`

Consulta el estado de un pedido. Requiere **las dos** cosas: correo y número.

| Parámetro | Tipo | Requerido | Notas |
| --- | --- | --- | --- |
| `email` | string | sí | Correo usado en la compra, comparado sin distinguir mayúsculas |
| `order_number` | string | sí | Con o sin `#`; `1009` y `#1009` son el mismo pedido |

Campos no declarados se rechazan con `-32602`.

Devuelve exactamente cinco campos:

```json
{
  "status": "In transit",
  "carrier": "USPS",
  "tracking_number": "9400366674684573766792",
  "estimated_delivery": "2026-08-25",
  "items": [
    {"title": "All Day Bundle Pack", "variant_title": "Default Title", "quantity": 1}
  ]
}
```

#### `search_products`

| Parámetro | Tipo | Requerido | Notas |
| --- | --- | --- | --- |
| `query` | string | sí | 1 a 100 caracteres; `%` y `_` se escapan, no son comodines |

Devuelve `query`, `count`, `currency` y hasta 10 productos, cada uno con sus
variantes e inventario.

#### `create_support_ticket`

La única herramienta que escribe.

| Parámetro | Tipo | Requerido | Notas |
| --- | --- | --- | --- |
| `email` | string | sí | Contacto para la respuesta |
| `subject` | string | sí | Hasta 200 caracteres |
| `description` | string | sí | Hasta 5000 caracteres, se guarda literal |

Devuelve `{"ticket_id": 6, "status": "open", "created_at": "..."}`.

### 2.6 Recursos

| URI | Contenido |
| --- | --- |
| `vibbo://policies/shipping` | Tiempos de procesamiento y entrega, transportistas, aduanas |
| `vibbo://policies/returns` | Ventana de 30 días, regla del 70%, tiempos de reembolso |
| `vibbo://policies/support-faq` | Respuestas aprobadas y lo que el asistente nunca debe decir |
| `vibbo://catalog/brewing` | Guía de preparación, ingredientes, alérgenos, almacenamiento |

El URI **nunca se convierte en una ruta de archivo**. El servidor mantiene una
tabla fija URI → nombre de archivo, así que un intento de traversal como
`vibbo://policies/../../secrets` no es una ruta que se normaliza mal: es una
llave que no está en el diccionario. Se rechaza con `-32602`.

### 2.7 Prompt

`support_triage` recibe `order_number`, `purchase_channel`,
`issue_description` y opcionalmente `evidence`. Devuelve dos mensajes: un turno
`assistant` con el procedimiento de triaje y un turno `user` con el reporte del
caso.

El texto del cliente se coloca entre marcadores explícitos
(`-----BEGIN CUSTOMER TEXT-----`) y el turno `assistant` declara que lo que
está adentro es material citado, no instrucciones. El servidor no intenta
detectar texto malicioso —esa es una batalla perdida— sino hacer inequívoca la
frontera entre instrucción y dato.

### 2.8 Códigos de error

| Código | Nombre | Cuándo |
| --- | --- | --- |
| `-32700` | Parse error | La línea no es JSON válido |
| `-32600` | Invalid Request | No es JSON-RPC 2.0, `id` de tipo inválido, arreglo, o llamada antes del handshake |
| `-32601` | Method not found | No existe el método |
| `-32602` | Invalid params | Argumentos faltantes, mal tipados o no declarados; herramienta, URI o prompt inexistente |
| `-32603` | Internal error | Excepción inesperada; el servidor responde igual |

### 2.9 Dos clases de falla

La distinción es deliberada y se ve en la captura:

- **Errores de protocolo** son respuestas de error JSON-RPC. Significan que el
  *cliente* mandó algo mal formado. El modelo nunca los ve.
- **Errores de ejecución** son respuestas exitosas con `isError: true`.
  Significan que la petición era válida pero la respuesta es mala noticia —no
  existe el pedido, no se encontró nada. El modelo sí los ve, para poder
  decirle al cliente qué revisar.

### 2.10 Diseño de seguridad

Tres reglas, aplicadas en la capa de datos (`src/db.py`), no en los handlers.

**1. No hay búsqueda por número de pedido solo.** `find_order()` exige también
el correo y compara ambos en una sola consulta. Sin esto, cualquiera podría
recorrer `#1001`, `#1002`, `#1003` y leer todos los pedidos de la tienda.

**2. Lo que el modelo no debe recibir no existe en el esquema.** No hay
columnas de dirección, teléfono ni pago en ninguna tabla. Es más fuerte que
filtrarlas en el handler: un campo que nunca se guardó no puede fugarse por un
cambio de código futuro, un log de depuración o un mensaje de error.
`get_order_status` declara un `outputSchema` con exactamente cinco campos, así
que la frontera es verificable por máquina y no una convención.

**3. Las fallas son explícitas e idénticas.** Un pedido inexistente y un correo
que no coincide devuelven el mismo mensaje. Decir *"ese pedido existe pero el
correo está mal"* confirmaría que el número es real, que es exactamente la
enumeración que este diseño evita.

**Limitación conocida.** El correo llega desde el modelo, que lo leyó del chat.
Sirve para una demo y está mal para producción: cualquiera puede decir que es
cualquier cliente. En un despliegue real el correo debe venir de una sesión
autenticada del anfitrión y la herramienta debería recibir un token. El diseño
del servidor no cambia; la pieza que falta está del lado del anfitrión.

---

## 3. Clasificación de los mensajes JSON-RPC capturados

### 3.1 Cómo se generó el tráfico

Capturar el chatbot directamente es poco útil: el modelo decide cuántas
llamadas hace y cuándo, así que ninguna captura se parece a otra y nada cuadra
con nada. Se usó `tests/capture_session.py`, que envía una secuencia fija, un
mensaje cada dos segundos, y escribe un manifiesto que nombra cada mensaje.

El script despierta el servidor **antes** de que empiece la captura. El plan
gratuito de Render suspende el servicio a los 15 minutos de inactividad y
tarda entre 30 y 50 segundos en despertar; sin esa precaución, cientos de
paquetes de arranque contaminarían la captura.

### 3.2 Qué se envió y qué se recibió

Captura `resumen-de-corrida.pcapng`, **8 de septiembre de 2026, 18:24 CST**.
Duración 57.9 s, 3109 paquetes en total, de los cuales **226 son tráfico con el
servidor remoto**. Cliente `10.122.232.79`, servidor `216.24.57.7`.

Todos los números de esta tabla se extrajeron del archivo de captura, no de los
registros de la aplicación. Los números de trama permiten localizar cada
mensaje en Wireshark.

| # | Tipo JSON-RPC | Método | `id` | Trama | Cuerpo | Trama resp. | HTTP | Cuerpo resp. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | solicitud | `initialize` | 1 | 1983 | 163 B | 2012 | 200 | 590 B |
| 2 | **notificación** | `notifications/initialized` | — | 2067 | 54 B | 2076 | **202** | **0 B** |
| 3 | solicitud | `ping` | 2 | 2157 | 52 B | 2166 | 200 | 41 B |
| 4 | solicitud | `tools/list` | 3 | 2226 | 58 B | 2233 | 200 | 4204 B |
| 5 | solicitud | `tools/call` | 4 | 2277 | 153 B | 2281 | 200 | 1079 B |
| 6 | solicitud | `resources/list` | 5 | 2343 | 62 B | 2354 | 200 | 1121 B |
| 7 | solicitud | `resources/read` | 6 | 2662 | 94 B | 2673 | 200 | 2137 B |
| 8 | solicitud | `prompts/list` | 7 | 2731 | 60 B | 2735 | 200 | 956 B |
| 9 | solicitud | `tools/call` | 8 | 2804 | 97 B | 2807 | 200 | 214 B (error `-32602`) |
| 10 | solicitud | `admin/shutdown` | 9 | 2886 | 62 B | 2895 | 200 | 137 B (error `-32601`) |

Antes de la secuencia, el despertador: `GET /` en la trama 399, respondido en
la 473 con `200` y 113 bytes. Al final, `DELETE /mcp` en la trama 2938,
respondido en la 2941 con `204 No Content`.

**Diez mensajes enviados, nueve respuestas JSON-RPC.** El que falta es la
notificación.

Los `id` en el cable van del 1 al 9 y son consecutivos, porque el cliente los
asigna con un contador protegido por un candado. La notificación no consume un
`id`: es el mensaje 2 de la secuencia pero no rompe la numeración, y ese salto
—del `id` 1 al `id` 2 entre tramas separadas por una notificación— es visible
en la captura.

**Una asimetría de tamaño.** Los cuerpos que salen son más compactos que los
que entran, y no es por el contenido. `src/jsonrpc.py` serializa con
`separators=(",", ":")`, sin espacios, porque el framing NDJSON del transporte
por stdio depende de que cada mensaje quepa en una línea. El transporte HTTP,
en `src/http_transport.py`, usa `json.dumps` con los separadores por omisión,
que agregan un espacio después de cada `:` y cada `,`. La diferencia ronda el
5% y explica por qué el resultado de `tools/list` mide 4204 bytes en el cable
cuando su forma compacta mide 3957.

### 3.3 Cómo verificar esta tabla

Los mismos números se reproducen desde la línea de comandos con `tshark`, que
viene con Wireshark. Con el registro de claves TLS:

```bash
tshark -r resumen-de-corrida.pcapng \
  -o tls.keylog_file:logs/tls-keys.log \
  -Y 'http && ip.addr==216.24.57.7' \
  -T fields -e frame.number -e http.request.method \
  -e http.request.uri -e http.response.code
```

Para los payloads JSON-RPC, el campo `http.file_data` sale en hexadecimal y hay
que decodificarlo:

```bash
tshark -r resumen-de-corrida.pcapng \
  -o tls.keylog_file:logs/tls-keys.log \
  -Y 'http.request.method=="POST"' \
  -T fields -e frame.number -e http.file_data
```

Cada valor decodificado desde hexadecimal es el mensaje JSON-RPC completo. La
presencia o ausencia del campo `id` es lo que clasifica el mensaje.

### 3.4 La clasificación que pide el enunciado

**Mensajes de sincronización.** Son los del ciclo de vida: `initialize` y
`notifications/initialized`. Juntos forman el handshake y nada se sirve hasta
que termina. Nótese que uno es solicitud y el otro notificación: `initialize`
necesita respuesta porque el cliente tiene que recibir la versión negociada y
las capacidades; `notifications/initialized` no negocia nada, solo avisa, así
que no lleva `id`.

`ping` también es sincronización en sentido amplio —verifica que el otro
extremo sigue vivo— pero es una solicitud con respuesta vacía.

**Solicitudes.** Todo `POST /mcp` cuyo cuerpo lleva un campo `id`. Cada una
recibe exactamente una respuesta. En esta captura: los mensajes 1 y 3 al 10.

**Respuestas.** Los cuerpos `200 OK`, cada uno repitiendo el `id` de la
solicitud que lo causó. Una respuesta lleva `result` **o** `error`, nunca
ambos y nunca ninguno.

**Notificaciones.** Cuerpo sin `id`. El mensaje 2 es el único. Se contesta con
`202 Accepted` y cuerpo vacío.

### 3.5 El hallazgo que hace legible la captura

La distinción entre solicitud y notificación **es visible en Wireshark sin
abrir un solo payload**:

- una solicitud vuelve como `200 OK` con cuerpo `application/json`
- una notificación vuelve como `202 Accepted` con cuerpo de cero bytes

Filtrando `http.response.code == 202` aparece exactamente un paquete de los
diez. Esa asimetría en la capa de aplicación HTTP es el reflejo directo de una
regla de la capa JSON-RPC: un mensaje sin `id` no se responde.

Sobre stdio la misma regla existe pero es invisible a un analizador de red,
porque no hay red: el mensaje se escribe en la tubería y simplemente nunca se
contesta.

### 3.6 Cuando HTTP y JSON-RPC discrepan a propósito

Los mensajes 9 y 10 volvieron con **`200 OK`** llevando un **error JSON-RPC**
(`-32602` y `-32601`).

No es una inconsistencia. Son dos capas informando cosas distintas: la
petición HTTP llegó al lugar correcto y se procesó, así que HTTP reporta éxito;
el contenido pedía una herramienta y un método que no existen, así que JSON-RPC
reporta la falla. Confundir los dos niveles es un error común al diseñar APIs
sobre HTTP.

---

## 4. Análisis por capas

Basado en la captura `resumen-de-corrida.pcapng`, paquete **473**.

### 4.1 El paquete analizado

El paquete 473 es la respuesta del servidor al `GET /` con el que el script
despierta el servicio, no un mensaje MCP. Se eligió porque es completo y
pequeño, y su aritmética de encapsulamiento se verifica a mano. Los mensajes
MCP (`POST /mcp`) tienen la misma estructura de capas con cuerpos mayores.

```
Frame 473: 81 bytes on wire (648 bits)
Arrival Time: Sep 8, 2026 18:24:23.089176400 Central America Standard Time
Epoch: 1788913463.089176400
Time since first frame: 7.858690 s
Encapsulation type: Ethernet (1)
```

### 4.2 Capa de enlace — Ethernet II

```
Ethernet II
  Destination: Intel_6d:62:9e (70:a6:cc:6d:62:9e)
  Source:      e6:4d:d9:93:17:ed
  Type:        IPv4 (0x0800)
```

**La observación importante:** la MAC de origen **no es la del servidor**. El
servidor está en `216.24.57.7`, a once saltos de distancia; su dirección física
no viaja por la red. La MAC de origen es la del último salto —el punto de
acceso o el router de la red— y la de destino es la de la tarjeta Wi-Fi Intel
de esta máquina.

Las direcciones MAC son **locales al enlace** y se reescriben en cada salto.
Las direcciones IP sobreviven todo el camino. Esa es la diferencia práctica
entre la capa 2 y la capa 3, y este paquete la muestra.

El primer octeto de la MAC de origen es `0xe6` = `1110 0110`. El segundo bit
menos significativo está en 1, lo que marca la dirección como **administrada
localmente**: no es una dirección asignada de fábrica a un fabricante, sino una
generada por el equipo o el controlador de la red inalámbrica.

`Type: 0x0800` es lo que le dice a la capa de enlace que entregue la carga al
módulo IPv4 y no a ARP (`0x0806`) o IPv6 (`0x86dd`).

### 4.3 Capa de red — IPv4

```
Internet Protocol Version 4
  Version:              4
  Header Length:        20 bytes (5)
  Differentiated Services Field: 0x00 (DSCP: CS0, ECN: Not-ECT)
  Total Length:         67
  Identification:       0x6a64 (27236)
  Flags:                0x2, Don't fragment
  Fragment Offset:      0
  Time to Live:         53
  Protocol:             TCP (6)
  Header Checksum:      0xd767
  Source Address:       216.24.57.7
  Destination Address:  10.122.232.79
```

**TTL 53.** Los sistemas operativos suelen inicializar el TTL en 64. Cada
router que reenvía el paquete lo decrementa en uno, así que este paquete
atravesó aproximadamente **once saltos** desde el centro de datos de Render
hasta esta máquina. El TTL es el mecanismo que evita que un paquete circule
para siempre si las tablas de rutas forman un ciclo.

**La dirección de destino `10.122.232.79` es privada** (RFC 1918). Esta máquina
no es alcanzable directamente desde Internet: está detrás de un NAT. El
servidor nunca vio esa dirección; le respondió a la IP pública del NAT de la
red, que reescribió el destino al entregar. Es la razón por la que un servidor
MCP local no puede exponerse sin más, y parte de por qué el despliegue remoto
resuelve un problema real.

**`Don't fragment` activo.** El emisor prohíbe que los routers intermedios
partan este paquete. Con 67 bytes está muy por debajo de cualquier MTU, pero la
bandera es la que hace funcionar el descubrimiento de MTU de camino: si un
enlace no puede pasar el paquete, debe descartarlo y avisar con ICMP en vez de
fragmentar.

**`Protocol: TCP (6)`** cumple el mismo papel que `Type` en Ethernet: dice a
qué módulo de la capa superior entregar la carga.

### 4.4 Capa de transporte — TCP

```
Transmission Control Protocol
  Source Port:            443
  Destination Port:       65510
  [Stream index:          26]
  [Stream Packet Number:  14]
  [Conversation completeness: Complete, WITH_DATA (63)]
  [TCP Segment Len:       27]
  Sequence Number:        3756    (relativo)
  Sequence Number (raw):  1856883949
  Next Sequence Number:   3783    (relativo)
  Acknowledgment Number:  746     (relativo)
  Acknowledgment (raw):   65720501
  Header Length:          20 bytes (5)
```

**Puerto de origen 443** identifica el servicio HTTPS. El **puerto de destino
65510** es efímero: el sistema operativo del cliente lo tomó del rango alto al
abrir la conexión y lo liberará al cerrarla. Junto con las dos IPs forman la
cuádrupla que identifica esta conexión de forma única.

**Los números de secuencia relativos y crudos.** El crudo, `1856883949`, es el
que viaja en el paquete; TCP inicializa la secuencia en un valor aleatorio para
dificultar la inyección de datos por parte de un tercero que quiera adivinar el
número. El relativo, `3756`, es una comodidad de Wireshark: le resta el valor
inicial para que se lea como "el byte 3756 de este flujo". `Next Sequence
Number: 3783` = 3756 + 27, exactamente los bytes de carga de este segmento.

**`Acknowledgment Number: 746`** confirma que el servidor recibió 745 bytes del
cliente. Ese es el reconocimiento acumulativo que da a TCP su fiabilidad: no
confirma paquetes sino posiciones en el flujo de bytes.

**`Stream index: 26`.** Wireshark numera las conexiones TCP desde cero, así que
esta captura contiene al menos **27 conexiones TCP distintas** para diez
mensajes MCP. La causa es que `urllib` no reutiliza conexiones: cada mensaje
abre su propio TCP, negocia su propio TLS, envía, y cierra. Es una ineficiencia
real de este cliente —un cliente de producción mantendría una conexión viva—
pero para el análisis resulta conveniente, porque cada mensaje trae su pila de
capas completa y aislada.

**`Conversation completeness: Complete, WITH_DATA`** significa que Wireshark vio
el ciclo entero de esta conexión: SYN, SYN-ACK, ACK, datos, y el cierre. La
captura no está truncada.

### 4.5 Entre transporte y aplicación — TLS 1.3

```
Transport Layer Security
[2 Reassembled TLS segments (452 bytes): #472(447), #473(5)]
```

Este es el punto donde el análisis habría fracasado sin preparación.

TLS 1.3 usa intercambio de claves efímero, con **secreto hacia el futuro**:
cada sesión negocia claves que se descartan al terminar. Tener la llave privada
del servidor **no alcanza** para descifrar una captura, a diferencia de lo que
ocurría con RSA estático en versiones antiguas de TLS. La única forma de leer
el tráfico es que uno de los extremos exporte sus claves de sesión.

`chatbot/http_client.py` configura `keylog_filename` en su contexto SSL desde
la variable `SSLKEYLOGFILE`, así que Python escribe los secretos de cada sesión
a un archivo. Wireshark los consume en **Preferences → Protocols → TLS →
(Pre)-Master-Secret log filename** y descifra.

Sin eso, la columna Protocolo diría `TLSv1.3 · Application Data` y no habría
nada que clasificar. Con eso, dice `HTTP/JSON`.

El registro TLS de este mensaje se reensambló de **dos segmentos TCP** (447
bytes en el paquete 472, 5 en el 473). Un registro TLS no coincide con un
segmento TCP: TLS entrega registros, TCP entrega un flujo de bytes, y el
reensamblado es responsabilidad del receptor.

### 4.6 Capa de aplicación — HTTP y JSON

```
Hypertext Transfer Protocol, has 2 chunks (including last chunk)
JavaScript Object Notation: application/json
```

Pestañas de bytes disponibles en Wireshark:

| Pestaña | Tamaño |
| --- | --- |
| Packet | 81 bytes |
| Decrypted TLS | 5 bytes |
| Reassembled TLS | 452 bytes |
| De-chunked entity body | **113 bytes** |

Los 113 bytes son exactamente el cuerpo del endpoint de salud:

```json
{"status": "ok", "server": "vibbo-mcp-server", "transport": "streamable-http", "endpoint": "/mcp", "sessions": 0}
```

**Transferencia por trozos.** El servidor no envió `Content-Length`; usó
`Transfer-Encoding: chunked`, y el cuerpo llegó en dos trozos más el trozo
final vacío que marca el fin. Es lo que permite a un servidor empezar a
responder antes de saber cuánto va a medir la respuesta.

En los mensajes MCP esta capa lleva, dentro del cuerpo, el mensaje JSON-RPC
completo: `jsonrpc`, `id`, `method`, `params` en las solicitudes; `jsonrpc`,
`id` y `result` o `error` en las respuestas.

### 4.7 El encapsulamiento, verificado

La aritmética cierra exactamente, y esa es la mejor demostración de qué
significa encapsular:

```
  14 bytes   encabezado Ethernet II  (6 destino + 6 origen + 2 tipo)
+ 20 bytes   encabezado IPv4
+ 20 bytes   encabezado TCP
+ 27 bytes   carga TCP (fragmento del registro TLS)
──────────
  81 bytes   = Frame Length
```

Y desde la perspectiva de IP:

```
  20 bytes   encabezado IPv4
+ 20 bytes   encabezado TCP
+ 27 bytes   carga
──────────
  67 bytes   = IPv4 Total Length
```

Cada capa trata a la de arriba como datos opacos y le agrega su propio
encabezado. Los 113 bytes de JSON que le importan a la aplicación viajaron
repartidos en dos segmentos TCP, cifrados dentro de registros TLS, dentro de
paquetes IP, dentro de tramas Ethernet. Ninguna capa necesitó saber nada de las
otras.

### 4.8 Lo que la captura no puede mostrar

Los tres servidores locales (`vibbo`, `filesystem`, `git`) se comunican por
**stdio**, que son tuberías del sistema operativo. **No hay paquetes que
capturar**: no hay capa de enlace, ni de red, ni de transporte. Wireshark no
los ve y no es una falla de la herramienta.

Fluyen exactamente los mismos mensajes MCP, con el mismo `jsonrpc`, los mismos
`id` y los mismos métodos, solo enmarcados uno por línea en vez de uno por
cuerpo de petición. Nunca tocan la pila de red.

Ese contraste es el resultado más interesante del proyecto: **el protocolo de
aplicación es idéntico y las capas debajo son completamente distintas.** Un
mismo `tools/call` puede ser una escritura a una tubería o once saltos de red
con cifrado; el servidor y el cliente ni se enteran.

En la captura sin filtrar también aparecieron **ARP** (`Who has 10.100.9.117?
Tell 10.100.0.1`) y **MDNS**, tráfico de descubrimiento de la red local, ajeno
al proyecto pero útil como evidencia de que la captura abarcaba la pila
completa y no solo HTTP.

---

## 5. Dificultades y cómo se resolvieron

### 5.1 PowerShell inyecta un BOM al canalizar

Alimentar el servidor con `Get-Content archivo.jsonl | python -m src` producía
`-32700 Parse error`. El archivo no tenía BOM; **la tubería de PowerShell lo
agrega**. Se resolvió leyendo stdin como `utf-8-sig`, que interpreta UTF-8
normal pero descarta un BOM inicial, más un `lstrip("﻿")` por línea.

### 5.2 Windows traduce los saltos de línea

El framing NDJSON depende de que cada mensaje termine en exactamente un
delimitador. Windows convierte `\n` en `\r\n` al escribir en modo texto, lo que
ponía dos. Se resolvió con `newline="\n"` en stdout del servidor y
`reconfigure(newline="\n")` en el stdin del subproceso desde el cliente —
`Popen` no acepta el argumento `newline`, hay que configurar el flujo después
de crearlo.

### 5.3 Un bug de veracidad en Python

El registro de mensajes MCP quedaba vacío. La causa: `Logbook` define
`__len__`, y Python usa `__len__` para evaluar veracidad cuando no hay
`__bool__`. Como el registro arranca vacío, `if self._logbook:` daba **False** y
nunca se registraba nada. Se corrigió comparando explícitamente contra `None`.

Es un error silencioso: no falla, simplemente no hace nada.

### 5.4 El modelo no podía alcanzar los recursos

Al preguntarle por la política de devoluciones, el modelo se iba al servidor
`filesystem` a buscar el archivo a mano: cuatro llamadas, y funcionó por
casualidad.

La causa está en el diseño de MCP: los recursos son *user-driven*, los elige el
anfitrión, y **el modelo no tiene ningún método para pedir uno**. Esa forma no
sirve para un chatbot, donde el modelo es quien se da cuenta a media frase de
que necesita la política.

Se resolvió dando a cada servidor con recursos una herramienta sintética
`read_resource` cuyo esquema lleva las URIs registradas como `enum`. El modelo
solo puede pedir una URI que el servidor ya declaró: gana alcance sin ampliar
lo alcanzable. La consulta pasó de cuatro llamadas a una.

### 5.5 El costo de reenviar las definiciones de herramientas

Con cuatro servidores hay 34 herramientas, y sus definiciones se reenvían en
cada turno y en cada vuelta del bucle. Una conversación de cinco turnos costaba
**$0.40**, lo que consumiría los $5 de crédito en doce corridas.

Se resolvió poniendo un punto de caché al final del prompt de sistema, que es
donde termina el bloque estable de la petición (el orden en el cable es
herramientas, sistema, mensajes). El costo bajó a **$0.0655** con 53,584 tokens
reutilizados de caché: **seis veces más barato**.

### 5.6 El servidor Git oficial no puede crear repositorios

El enunciado pide un escenario donde el chatbot cree un repositorio, escriba un
README, lo agregue y commitee. El servidor `mcp-server-git` expone doce
herramientas y **ninguna es `git_init`**: todas operan sobre un repositorio que
ya existe, y el servidor se lanza con `--repository` apuntando a uno.

`tests/demo_official_servers.py` crea el directorio y corre `git init` antes de
entregarle el repositorio al chatbot. Escribir el archivo, indexarlo,
commitearlo y leer el log se hacen por MCP. El script verifica el resultado con
comandos `git` directos, para no depender de lo que el modelo dijo que hizo.

### 5.7 Google Cloud exige un prepago de $30

El enunciado enlaza un tutorial de Cloud Run y menciona que la prueba gratuita
no requiere tarjeta. En Guatemala, Google pide un **prepago único de $30 USD**
(reembolsable al cerrar la cuenta de facturación) además de la tarjeta.

Se desplegó en **Render**, cuyo plan gratuito no pide método de pago. El
`Dockerfile` sirvió sin cambios; se agregó `render.yaml` para que el despliegue
sea reproducible desde el repositorio y no una secuencia de clics. Las
instrucciones de Cloud Run quedaron documentadas como alternativa.

El costo: el plan gratuito suspende el servicio a los 15 minutos de
inactividad, con 30 a 50 segundos de arranque en frío. Se compensa despertando
el servicio antes de cualquier demo, y con `STARTUP_TIMEOUT = 120` en el
cliente HTTP.

### 5.8 Los tres filtros de Wireshark que se parecen

Se perdió tiempo escribiendo sintaxis BPF (`tcp port 443`) en el filtro de
visualización y en la barra de búsqueda, que esperan sintaxis de Wireshark
(`tcp.port == 443`). Son tres campos visualmente similares con propósitos
distintos:

| Campo | Sintaxis | Cuándo actúa |
| --- | --- | --- |
| Capture → Options | BPF | Descarta paquetes antes de guardarlos |
| Barra ancha superior | Wireshark | Esconde paquetes ya capturados |
| Ctrl+F | texto | Busca contenido |

La red del campus generaba tanto MDNS y ARP que una captura sin filtrar llegó a
390,000 paquetes. La solución práctica fue capturar sin filtro por 25 segundos
y filtrar `http` después.

### 5.9 La base de datos remota es efímera

El sistema de archivos del contenedor está en memoria y se descarta al
reiniciar. Un ticket creado a través del servidor remoto se pierde, y dos
instancias no comparten tickets.

No es un bug: es el comportamiento correcto de un contenedor sin estado. La
solución real es sacar la base del contenedor, y eso tocaría **solo
`src/db.py`** — sigue sin ser un problema de protocolo. Las lecturas no se
afectan porque el seed viene horneado en la imagen, idéntico en toda instancia.

---

## 6. Conclusiones

**Implementar el protocolo a mano fue lo que hizo entendible el proyecto.**
Usar un SDK habría producido el mismo chatbot en una fracción del tiempo y no
habría enseñado nada sobre por qué `initialize` necesita respuesta y
`notifications/initialized` no, ni por qué el batching se eliminó de la
especificación, ni qué se rompe cuando Windows traduce un salto de línea.
Escribir el parseo obliga a decidir qué hacer con cada caso raro, y esas
decisiones son el protocolo.

**La separación entre transporte y lógica de negocio dejó de ser teoría.** El
requisito de desplegar el mismo servidor de forma remota es una prueba: si las
capas están mezcladas, hay que reescribir. Aquí se agregó un archivo nuevo y
una bandera, y las tres herramientas siguieron funcionando sin cambios. La
prueba de que funcionó es que el chatbot llama al servidor local y al remoto
con el mismo código de `session.py`, y ese código no sabe cuál es cuál.

**JSON-RPC sobre stdio y sobre HTTP no son el mismo problema.** Sobre stdio, un
proceso es un cliente y el estado del ciclo de vida es implícito. Sobre HTTP no
hay estado, así que hubo que agregar sesiones con `Mcp-Session-Id` y un
registro con expiración. Esa diferencia no está en la capa de aplicación —los
mensajes son idénticos— sino en lo que el transporte garantiza. Un transporte
que garantiza menos obliga al protocolo a decir más.

**El análisis de capas demostró algo que en clase suena abstracto.** La
aritmética del paquete 473 cierra exactamente: 14 + 20 + 20 + 27 = 81 bytes. Los
113 bytes de JSON que le importaban a la aplicación viajaron repartidos en dos
segmentos TCP, cifrados en registros TLS, dentro de paquetes IP con TTL 53
—once saltos— dentro de tramas Ethernet cuya MAC de origen no era la del
servidor sino la del último router. Cada capa trató a la de arriba como datos
opacos. Verlo en un paquete real es distinto a verlo en un diagrama.

**El contraste stdio contra HTTP fue el resultado más útil.** Los tres
servidores locales no generan un solo paquete: no tienen capa de enlace, ni de
red, ni de transporte. El mismo `tools/call`, con el mismo `id` y el mismo
método, es una escritura a una tubería en un caso y once saltos cifrados en el
otro. El protocolo de aplicación es idéntico; todo lo de abajo cambia por
completo. Eso es lo que significa que un protocolo esté en la capa de
aplicación.

**TLS 1.3 cambió lo que significa capturar tráfico.** Con secreto hacia el
futuro, tener la llave del servidor ya no basta: la única forma de leer una
captura es que un extremo colabore exportando sus claves de sesión. Es una
buena noticia para la privacidad y un problema real para el diagnóstico, y
explica por qué las herramientas modernas de depuración se instrumentan en el
cliente en vez de escuchar el cable.

**Lo que quedó pendiente y por qué importa.** El correo del cliente llega desde
el texto del chat, no de una sesión autenticada, así que cualquiera puede
reclamar ser cualquier cliente. Es la limitación más grave del sistema y no se
arregla en el servidor: el servidor ya minimiza lo que devuelve y ya se niega a
buscar por número de pedido solo. La pieza que falta está en el anfitrión, y
reconocer de qué lado vive un problema de seguridad es parte de haberlo
entendido.

---

## Anexos

| Documento | Contenido |
| --- | --- |
| `README.md` | Instalación, uso, referencia completa de herramientas, recursos y prompt |
| `DEPLOYMENT.md` | Despliegue remoto y procedimiento de captura |
| `tests/manual_requests.md` | Cada método a mano, con todas las rutas de error |
| `tests/demo_session.jsonl` | Sesión completa: 19 mensajes, 17 respuestas |
| `tests/demo_official_servers.py` | Escenario con los servidores oficiales, verificado en disco |
| `tests/capture_session.py` | Generador de tráfico para la captura |
| `logs/capture-manifest.md` | Manifiesto de la captura analizada |
| `resumen-de-corrida.pcapng` | La captura |
