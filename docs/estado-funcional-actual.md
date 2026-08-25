# Estado funcional actual

- Fecha: 2026-08-24
- Issue: [#1 - Registrar el estado funcional actual](https://github.com/LeoFolino/LigaDeFutbol/issues/1)
- Responsable: `miccazerba`
- Rama: `issue-1-estado-funcional`
- Commit evaluado: `1a16b0b340d0c1b203ed8470a7e52c42c9f9c50c`

## Entorno evaluado

- Sistema operativo: macOS 26.2 (Apple Silicon)
- Base: `app/data/global_players.sqlite3`, descargada mediante Git LFS
- Tamaño de la base: 111.181.824 bytes (106 MB)
- Servidor: Uvicorn 0.35.0 en `http://127.0.0.1:8000`
- Entorno usado para la comprobación: Python 3.9.6 con las dependencias de
  `requirements.txt` y `eval-type-backport==0.4.0`

El código utiliza anotaciones como `int | None`, por lo que requiere Python
3.10 o posterior. En Python 3.9 solo inicia al instalar adicionalmente
`eval-type-backport`. En esta máquina las instalaciones de Python 3.11 y 3.12
de Homebrew presentaron un error local de enlace de `pyexpat`; no es un error
producido por la aplicación.

## Cantidades registradas

Consulta realizada directamente sobre SQLite:

| Entidad | Cantidad |
| --- | ---: |
| Jugadores (`global_players`) | 19.369 |
| Equipos (`teams`) | 16 |
| Asignaciones (`team_players`) | 315 |

La vista Base global muestra 18.919 jugadores de forma predeterminada. La
diferencia corresponde a 450 registros cuyo `player_kind` es
`generic_unlicensed`, que la consulta normal excluye intencionalmente. Los
19.369 registros permanecen en SQLite.

## Integridad de SQLite

- `PRAGMA integrity_check`: `ok`
- Asignaciones cuyo equipo no existe: 0
- Asignaciones cuyo jugador no existe: 0
- Tablas presentes: `global_players`, `import_metadata`, `team_players` y
  `teams`

Consultas usadas:

```sql
SELECT COUNT(*) FROM global_players;
SELECT COUNT(*) FROM teams;
SELECT COUNT(*) FROM team_players;
PRAGMA integrity_check;

SELECT COUNT(*)
FROM team_players tp
LEFT JOIN teams t ON t.id = tp.team_id
WHERE t.id IS NULL;

SELECT COUNT(*)
FROM team_players tp
LEFT JOIN global_players gp ON gp.id = tp.player_id
WHERE gp.id IS NULL;
```

## Funcionalidades verificadas

### Equipos

- La pestaña carga y muestra los 16 equipos.
- La API `GET /api/teams` responde HTTP 200.
- La interfaz contiene selección y administración de equipos, carga de
  escudos, planteles, asignación y remoción de jugadores.
- Se muestran presupuesto inicial, gasto de mercado, salarios y presupuesto
  restante.
- Las rutas de alta, edición, eliminación, logos y planteles están publicadas
  en OpenAPI.

### Base global

- La pestaña carga sin errores JavaScript.
- Se renderizan 100 jugadores en la primera página.
- La interfaz informa `Página 1/190 - 18919 resultados` con el filtro normal.
- La API `GET /api/global-players` responde HTTP 200.
- Están disponibles búsqueda, posición, media mínima, valor máximo, estado de
  Transfermarkt, paginación, alta, edición, eliminación e importación CSV.
- Se exponen acciones para actualizar datos desde SoFIFA y Transfermarkt.

### Calculadora

- La pestaña carga sin errores JavaScript.
- Sin jugadores muestra presupuesto inicial de `$300M` y totales en cero.
- Permite buscar, agregar, quitar y limpiar jugadores y calcula mercado,
  salarios, total, media y distribución por líneas.
- La selección se persiste en `localStorage` mediante una clave propia.
- Las operaciones de la calculadora modifican únicamente
  `calculatorState`/`localStorage`; no invocan las rutas que asignan jugadores
  a equipos.

La prueba visual automatizada abrió las tres pestañas usando Google Chrome y
terminó sin errores de página. Para preservar la fotografía original, no se
ejecutaron operaciones destructivas de alta, edición o eliminación sobre la
base principal.

## Funcionalidad Draft

No existe una pestaña, botón, endpoint ni lógica activa de Draft. La navegación
actual contiene únicamente:

1. Equipos
2. Base global
3. Calculadora

La búsqueda de `draft` en `app`, `frontend`, scripts y documentación no produjo
referencias funcionales.

## Backup recuperable

- Archivo: `global_players-issue-1-2026-08-24.sqlite3`
- Ubicación local, fuera del repositorio:
  `/Users/micca/backups/LigaDeFutbol/global_players-issue-1-2026-08-24.sqlite3`
- Tamaño: 111.181.824 bytes (106 MB)
- SHA-256:
  `4c67354bf67c0eb4f4611b0b04bfc46651045a6488d39bec478f314da97b886d`
- Integridad del backup: `ok`
- Prueba de restauración: `ok`
- Cantidades restauradas: 19.369 jugadores, 16 equipos y 315 asignaciones

Creación:

```bash
mkdir -p ~/backups/LigaDeFutbol
sqlite3 app/data/global_players.sqlite3 \
  ".backup '$HOME/backups/LigaDeFutbol/global_players-issue-1-2026-08-24.sqlite3'"
shasum -a 256 \
  ~/backups/LigaDeFutbol/global_players-issue-1-2026-08-24.sqlite3
```

Restauración y verificación:

```bash
sqlite3 ~/backups/LigaDeFutbol/global_players-issue-1-2026-08-24.sqlite3 \
  ".backup '/tmp/liga-restaurada-issue-1.sqlite3'"
sqlite3 /tmp/liga-restaurada-issue-1.sqlite3 "PRAGMA integrity_check;"
```

El backup no se incluye en Git para evitar duplicar una base binaria de 106 MB.

## Resultado

El estado actual queda reproducible y respaldado. Equipos, Base global y
Calculadora cargan correctamente; SQLite es íntegra; no existen asignaciones
huérfanas; y no hay funcionalidad Draft activa.
