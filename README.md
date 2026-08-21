# Liga Profesional de Futbol | Manager

Gestor local de equipos, planteles y jugadores de la liga.

## Funciones

- Presupuesto inicial de `$300M`.
- Calculo automatico de sueldo por media SoFIFA.
- Costo total por jugador: valor Transfermarkt + sueldo.
- Administracion de equipos y sus planteles.
- Busqueda de jugadores por nombre, posicion, club o ID SoFIFA.
- Calculadora de plantel sin modificar los equipos reales.
- Links editables a SoFIFA y Transfermarkt.
- Base global de jugadores.
- Alta, edicion, busqueda y borrado de jugadores globales.
- Registro de version SoFIFA, fecha de valor Transfermarkt, tags y notas por jugador.

## Arranque

Necesitas Python instalado y disponible en la terminal que uses.

Si usas Git Bash, entra a la carpeta del proyecto y corre:

```bash
cd /ruta/al/LigaDeFutbol
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Si `python` no existe en Git Bash, proba reemplazarlo por `python3`:

```bash
python3 -m venv .venv
source .venv/Scripts/activate
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En esta PC Python 3.12 esta instalado en:

```text
C:\Users\leokb\AppData\Local\Programs\Python\Python312\python.exe
```

Si Windows sigue abriendo el alias de Microsoft Store, podes crear el entorno usando esa ruta completa:

```bash
/c/Users/leokb/AppData/Local/Programs/Python/Python312/python.exe -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En PowerShell seria:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Para usar la app con la base SQLite cargada, conviene correr Uvicorn sin `--reload`. El modo reload reabre procesos mientras detecta cambios y puede volver lentas las operaciones de asignacion de jugadores/equipos.

Si el puerto queda ocupado por una ejecucion anterior, podes cortarla desde PowerShell:

```powershell
Stop-Process -Name python -Force
```

O usar otro puerto:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Si `python -V` abre el alias de Microsoft Store, instala Python desde `python.org` o con:

```powershell
winget install Python.Python.3.12
```

Despues abrir:

```text
http://127.0.0.1:8000
```

## Uso en vivo

1. Crear o seleccionar un equipo.
2. Buscar jugadores en la base global y asignarlos al plantel.
3. Consultar presupuesto, valores de mercado, sueldos y atributos.
4. Usar la Calculadora para probar un plantel sin cambiar las asignaciones reales.

## Base global de jugadores

La pestana `Base global` funciona como registro maestro de jugadores. Ahi se pueden cargar y consultar jugadores aunque todavia no esten asignados a un equipo.

Datos disponibles por jugador:

- Nombre, posicion, club real y nacionalidad.
- ID, URL y version de SoFIFA.
- Media y atributos principales.
- URL, valor, moneda y fecha de consulta de Transfermarkt.
- Sueldo calculado por media y costo total.
- Tags y notas.

Los datos principales se guardan en `app/data/global_players.sqlite3`.

## Importar jugadores por CSV

Para evitar bloqueos de SoFIFA/Cloudflare, la app puede importar un dataset completo desde CSV a SQLite.

1. Descargar un CSV de jugadores FC 26/SoFIFA.
2. Guardarlo como:

```text
data/raw/players.csv
```

3. Importarlo desde la pestana `Base global` con el boton `Importar CSV`.

Tambien se puede importar por terminal:

```powershell
.\.venv\Scripts\python.exe scripts\import_players.py --csv data\raw\players.csv --source-dataset fc-26 --source-version 2026-07-16
```

La base importada queda en:

```text
app/data/global_players.sqlite3
```

La app usa SQLite automaticamente cuando ese archivo existe. Si no existe, inicializa un almacenamiento JSON vacio.

## Actualizar jugadores desde SoFIFA con Playwright

SoFIFA bloquea automatizaciones con Cloudflare. El script de actualizacion abre un navegador real y espera que completes la verificacion humana cuando aparezca.

Por defecto se usa la actualizacion oficial de la liga:

```text
SoFIFA FC 26 - Jul 16, 2026 - version URL 260045
```

La version se puede cambiar desde `.env`. Copiar `.env.example` a `.env` y ajustar:

```text
SOFIFA_VERSION_URL_PART=260045
SOFIFA_VERSION_LABEL=Jul 16, 2026
SOFIFA_LOCALE=es-ES
```

Preparacion:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
git clone https://github.com/1erkandogan/sofifa-web-scraper .external/sofifa-web-scraper
```

Abrir Chromium de Playwright e iniciar sesion:

```powershell
.\.venv\Scripts\python.exe scripts\sofifa_login_playwright.py
```

En Git Bash:

```bash
./.venv/Scripts/python.exe scripts/sofifa_login_playwright.py
```

Ese login queda guardado en `.external/playwright-sofifa-profile`, el mismo perfil que usa el actualizador.

Probar con un jugador:

```powershell
.\.venv\Scripts\python.exe scripts\update_sofifa_playwright.py --ids 277846 --limit 1
```

Usar Chrome real con tu sesion iniciada:

1. Cerrar Chrome completamente.
2. Abrirlo con remote debugging:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:LOCALAPPDATA\Google\Chrome\User Data"
```

3. Verificar que estes logueado en SoFIFA en ese Chrome.
4. Ejecutar:

```powershell
.\.venv\Scripts\python.exe scripts\update_sofifa_playwright.py --ids 277846 --limit 1 --cdp-url http://127.0.0.1:9222
```

En Git Bash:

```bash
python scripts/update_sofifa_playwright.py --ids 277846 --limit 1 --cdp-url http://127.0.0.1:9222
```

Actualizar una tanda chica:

```powershell
.\.venv\Scripts\python.exe scripts\update_sofifa_playwright.py --min-overall 80 --limit 25 --skip-updated
```

Actualizar en tandas de 1000 usando Chrome real:

```powershell
.\.venv\Scripts\python.exe scripts\update_sofifa_playwright.py --limit 1000 --skip-updated --workers 2 --cdp-url http://127.0.0.1:9222 --delay-seconds 0.2 --post-verify-wait 0.5
```

En Git Bash:

```bash
python scripts/update_sofifa_playwright.py --limit 1000 --skip-updated --workers 2 --cdp-url http://127.0.0.1:9222 --delay-seconds 0.2 --post-verify-wait 0.5
```

Durante la ejecucion el script muestra worker, jugador actual, actualizados, fallidos, tiempo transcurrido, ETA y promedio final por jugador. Si Cloudflare empieza a pedir verificaciones seguido, bajar a `--workers 1`. Si va estable, probar `--workers 3`.

Actualizar todos los jugadores es posible en tandas, pero puede tardar muchas horas y dependera de Cloudflare. No uses proxies ni automatizaciones para saltar verificaciones.

## Actualizar valores desde Transfermarkt

Cada jugador global puede actualizar su valor desde el link de Transfermarkt guardado en la base. Ese valor reemplaza el valor de SoFIFA para calcular el costo de la liga:

```text
costo total = valor Transfermarkt + sueldo por media SoFIFA
```

Desde la UI usar el boton `Valor TM` en la fila del jugador. La app valida que el nombre de Transfermarkt coincida razonablemente con el jugador antes de guardar.

Para actualizar varios desde la app, entrar en `Base global`, elegir `Jugadores por tanda` y usar `Actualizar valores TM`. Con `Solo pendientes` activo no pisa jugadores que ya tengan consulta Transfermarkt guardada.

Para actualizar en tandas:

```bash
python scripts/update_transfermarkt_values.py --limit 100 --skip-updated --delay-seconds 1 --stop-after-consecutive-failures 5
```

El limite por defecto sale de `.env`:

```text
TRANSFERMARKT_BATCH_LIMIT=100
```
