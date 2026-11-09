# CLAUDE.md — VideoEditor

Contexto para Claude Code al trabajar en este proyecto (en el PC del usuario, normalmente `C:\VideoEditor` en Windows).

## Quién es el usuario y cómo hablarle
- Hace **edits verticales de gimnasio / fitness** (Reels/TikTok) grabados de noche en gimnasios oscuros con luces LED, sincronizados con la música.
- **Habla siempre en español**, claro y sin jerga. No es programador: explica qué haces y qué resultado esperar, no el código.
- Cuando generes un video, di **dónde quedó el archivo** (ruta completa) y un resumen de cómo está montado (cortes, transiciones, efectos, LUT).

## Qué es el proyecto
Editor de video basado en FFmpeg + Python (numpy). Aprende "estilos" de videos de referencia (estructura, técnicas, ritmo en beats, color), analiza la música (tempo, secciones, drop, golpes) y monta edits nuevos con los clips del usuario. Hay interfaz web (`serve`) y línea de comandos; tú usas la **línea de comandos**.

Guías para el usuario: `docs/GUIA_PC.md` (instalación/uso) y `docs/ENSENAR_TECNICAS.md` (aprender técnicas). Referencia completa: `README.md`.

## Cómo ejecutar (Windows, desde la carpeta del proyecto)
Usa siempre el Python del entorno del proyecto, sin activar nada:

```
.venv\Scripts\python.exe -m videoeditor <comando> ...
```
(en macOS/Linux: `.venv/bin/python -m videoeditor ...`). Si algo falla por instalación: `.venv\Scripts\python.exe -m videoeditor doctor`.

## Dónde están los archivos del usuario
Carpeta de trabajo: `workspace\` (dentro del proyecto). La misma que usa la interfaz web.

| Carpeta | Contenido |
|---|---|
| `workspace\references\` | videos editados de referencia para **aprender** técnicas/estilos |
| `workspace\clips\` | tomas del usuario para montar |
| `workspace\music\` | canciones |
| `workspace\styles\` | estilos aprendidos (`learn` los guarda aquí) |
| `workspace\luts\` | LUTs `.cube` propios del usuario |
| `workspace\projects\` | proyectos JSON (editables) |
| `workspace\renders\` | **videos terminados**: guarda aquí los edits que generes |

Si el usuario dice que ha dejado archivos "en las carpetas", míralas con `dir workspace\clips` etc. Si faltan carpetas, créalas.

## Comandos principales
```
python -m videoeditor analyze workspace\references\ref.mp4                # informe plano a plano: cortes, transiciones, efectos, beats
python -m videoeditor analyze ref.mp4 --template workspace\projects\copia.json   # proyecto con los mismos tiempos
python -m videoeditor music workspace\music\cancion.mp3                    # secciones, drop, energía, golpes
python -m videoeditor learn workspace\references\ref1.mp4 [ref2.mp4 ...] --name "Nombre estilo"
python -m videoeditor styles                                               # estilos disponibles (incluido: "Gym noche")
python -m videoeditor luts                                                 # LUTs: noche_azul, acero_frio, oro_gym, teal_orange, bleach_bypass, phonk_rojo, mate_film, monocromo_contraste, neon_morado
python -m videoeditor lut-preview workspace\clips\c1.mp4 -o workspace\renders\luts.jpg   # ver cada LUT sobre un clip
python -m videoeditor auto --style "Nombre estilo" -m workspace\music\cancion.mp3 workspace\clips\*.mp4 ^
       --duration 15 --lut style -o workspace\renders\edit.mp4               # edit automático (genera también edit.json)
python -m videoeditor render workspace\renders\edit.json [--preview]       # volver a renderizar tras editar el JSON
python -m videoeditor list                                                 # efectos, transiciones, looks
```
Opciones útiles de `auto`: `--duration S`, `--music-start S` (por defecto automático: el clímax del estilo cae en el drop), `--lut ID|style` + `--lut-strength 0-1`, `--cinematic` (halo + grano), `--keep-order`, `--no-sfx`, `--no-accents`, `--preview` (rápido, media resolución), `--no-render` (solo JSON). En Windows `cmd` el comodín `*.mp4` no lo expande la shell: **lista los clips uno a uno** (o usa PowerShell para obtener la lista).

## Flujo típico que pedirá el usuario
1. **Analizar un video de referencia**: `analyze` → explícale en español qué técnicas usa, en qué segundos, con qué ritmo (beats) y color. Luego `learn` con un nombre (si hay varias referencias del mismo estilo, apréndelas con el mismo nombre: se guardan como variantes).
2. **Montar un edit** (~15 s) con sus clips y su canción usando lo aprendido: `music` para ver la estructura de la canción, después `auto --style ... --duration 15 ...`. Primero con `--preview` si quiere ver rápido; al final sin `--preview`.
3. Revisa el aviso de **revisión de calidad** que imprime el render (cortes fuera de beat, congelados, audio). Si hay problemas, corrige el proyecto JSON (`workspace\renders\edit.json`) y vuelve a renderizar con `render`.
4. Para combinar estilos, cambiar transiciones/efectos de un plano o ajustar tiempos: edita el JSON del proyecto (formato en `README.md` → "Formato del proyecto"), manteniendo los cortes en los beats de `info.beats`.

## Si el usuario quiere una técnica que la app no reconoce
`analyze` no la listará (la verá como corte normal). Para añadirla: render (efecto/transición en `effects.py`/`render.py`/`project.py`), detector (`analyze.py`/`motionfx.py`), aprendizaje/aplicación (`style.py`), test de ida y vuelta en `tests/` (renderizar con la técnica → analizar → encontrarla en el segundo correcto, y no verla en videos sin ella). Ver `docs/ENSENAR_TECNICAS.md`.

## Mapa del código (`videoeditor/`)
`project.py` (modelo/validación JSON) · `render.py` (render por segmentos + xfade/concat + audio) · `effects.py` (looks y efectos) · `luts.py` (LUTs .cube) · `beats.py`/`music.py` (ritmo y estructura musical) · `analyze.py`/`motionfx.py` (análisis de referencias) · `style.py` (aprender/aplicar estilos guiado por la música) · `auto.py` (plantillas fijas, mejor tramo de cada clip) · `qa.py` (revisión de calidad) · `sfx.py` (efectos de sonido sintetizados) · `segment.py` (recorte de personas, MediaPipe opcional) · `server.py` + `web/` (interfaz) · `ff.py` (ffmpeg, escapado de rutas) · `sysutil.py` · `doctor.py`.

## Reglas al cambiar código
- Tests: `python -m unittest discover -s tests` (≈2 min, todos deben pasar antes de dar algo por terminado). Tras tocar `web/app.js`: `node --check videoeditor/web/app.js` si hay Node.
- Textos de interfaz, mensajes y documentación **en español**. Solo dependencias numpy + FFmpeg (MediaPipe y Tesseract opcionales).
- Lecciones aprendidas de este proyecto:
  - Rutas dentro de filtros de FFmpeg: siempre con `ff.filter_value()` (Windows `C:\...`, comillas, acentos).
  - Zoom centrado: usar `zoompan` (scale+crop dinámicos anclan arriba a la izquierda).
  - Mezclas `blend` en RGB (`format=gbrp`), si no tiñen de morado.
  - Las transiciones no desplazan los tiempos: cada segmento empieza en la suma de los anteriores; el corte (o el aterrizaje en slides/subject_drop) cae en el beat.
  - Comprueba visualmente: extrae fotogramas con `ffmpeg -vf "fps=4,scale=120:-2,tile=..."` y míralos.
- Commits con mensajes claros en español; no hagas push salvo que el usuario lo pida.
