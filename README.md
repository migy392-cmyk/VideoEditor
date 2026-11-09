# VideoEditor

Editor de video por proyectos JSON, basado en FFmpeg, para hacer edits verticales
de gym / fitness sincronizados con la música, del estilo del video de referencia.

**Guías paso a paso:**
- [Usarlo en tu PC (Windows, macOS, Linux)](docs/GUIA_PC.md)
- [Enseñarle técnicas nuevas con tus videos](docs/ENSENAR_TECNICAS.md)

## Lo que hace el video de referencia

Lo analizamos con `python -m videoeditor analyze`:

| Tiempo        | Qué pasa                                                                 |
|---------------|--------------------------------------------------------------------------|
| 0.00 – 0.50   | Pantalla en negro hasta el primer beat                                    |
| 0.50 – 3.67   | **Layout de 3 paneles apilados**: cada panel entra en un beat (cada 2 beats ≈ 1 s) |
| 3.67 – 3.97   | El siguiente plano **cae desde arriba** sobre los paneles                 |
| 3.97 – 13.93  | Planos a pantalla completa con **cortes secos en el beat** (cada ~4 beats) |
| 13.60 – 13.93 | **Glitch digital** (bloques, ruido verde, separación RGB)                 |
| 13.93 – 14.07 | Negro                                                                     |

La música va a ~119 BPM (1 beat ≈ 0.5 s), el video es 1080×1920 y el look es
nocturno: sombras azuladas, luces cálidas, contraste alto.

## Instalación

Hace falta **Python 3.10+** y **FFmpeg** (`ffmpeg` y `ffprobe` en el PATH) en su versión
**completa ("full")**: necesita los filtros `zscale` (videos HDR de iPhone), `vidstab`
(estabilización), `drawtext` (textos) y el codec `libx264`. Si falta alguno, el editor sigue
funcionando y avisa de lo que se desactiva.

### Windows 10/11

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg          # build "full"; alternativa: choco install ffmpeg-full
# abre una terminal nueva para que se actualice el PATH
cd VideoEditor
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m videoeditor doctor        # comprueba que todo está bien
python -m videoeditor serve         # abre el navegador solo
```

Instalación manual de FFmpeg: descarga `ffmpeg-release-full.7z` de
<https://www.gyan.dev/ffmpeg/builds/> (no la "essentials"), descomprímela y añade su carpeta
`bin` al PATH.

### macOS

```bash
brew install python ffmpeg
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m videoeditor doctor
python -m videoeditor serve
```

### Linux (Debian/Ubuntu)

```bash
sudo apt install python3 python3-venv ffmpeg
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m videoeditor doctor
python -m videoeditor serve
```

### Opcional

- `pip install -r requirements-extra.txt` instala **mediapipe**, que sólo se usa para recortar a
  la persona (transición `subject_drop`). El modelo (~16 MB) se descarga solo la primera vez. Sin
  él, esa transición se convierte en un deslizamiento normal. En Linux necesita además
  `sudo apt install libegl1 libgles2`.
- `tesseract` (macOS: `brew install tesseract tesseract-lang`; Linux: `apt install tesseract-ocr
  tesseract-ocr-spa`; Windows: `winget install UB-Mannheim.TesseractOCR`) permite leer los textos
  de los videos de referencia.
- `pip install .` instala además el comando `videoeditor` (equivale a `python -m videoeditor`).

`python -m videoeditor doctor` muestra una lista con lo que está instalado y, de lo que falte,
cómo arreglarlo en tu sistema. Las cachés (LUTs, música analizada, efectos de sonido, modelo) van
a `%LOCALAPPDATA%\videoeditor\Cache` (Windows), `~/Library/Caches/videoeditor` (macOS) o
`~/.cache/videoeditor` (Linux); se puede cambiar con la variable `VIDEOEDITOR_CACHE`.

## Primero, la música

Antes de montar nada, el editor analiza la canción (`python -m videoeditor music cancion.mp3`,
o *Analizar* en la Biblioteca):

- **tempo exacto** y beats sin desfase a lo largo de toda la canción, y primer tiempo de cada compás;
- **secciones**: intro, subida, drop, bajada y final, con su energía;
- el **drop principal**, los **golpes fuertes** y las pausas.

Y edita en función de ella:

- si no le dices dónde empezar la canción, elige el inicio para que el **momento fuerte del
  estilo caiga en el drop** (en el estilo incluido: la persona aterriza justo en el drop);
- si no le dices la duración, **termina al acabar la sección del drop**, en un compás;
- **corta en cada cambio de sección** de la canción y pone **más cortes** donde la canción
  tiene más energía que la referencia y **menos** donde tiene menos;
- **cámara lenta** en las bajadas y una **rampa de cámara lenta** en el beat anterior al drop;
- **flash + zoom de golpe en el drop** y zooms en los golpes fuertes;
- **efectos de sonido** sintetizados: whoosh en las transiciones, impacto en el drop,
  subida de tensión los 2 compases antes y sonido de glitch.

## Interfaz web

```bash
python -m videoeditor serve          # abre http://127.0.0.1:8765 en el navegador (--no-open para evitarlo)
```

Todo se guarda en la carpeta `workspace/` (cámbiala con `-w otra_carpeta`).

1. **Biblioteca**: sube tus clips, canciones y **videos de referencia** (edits que te gustan).
2. **Estilos**: elige referencias, ponle un nombre y pulsa *Aprender*. Verás la
   "receta" del estilo: planos, paneles, transiciones y efectos medidos en beats.
   Si aprendes con un nombre que ya existe, el video se añade como otra variante
   del mismo estilo y el color se promedia.
3. **Nuevo edit**: elige estilo, canción y clips (en orden), un **LUT** de color (miniaturas) y pulsa *Crear edit*. Verás el
   mapa de la canción. Opciones: editar según la canción, zooms y flashes automáticos,
   efectos de sonido y look de cine.
4. **Editor**: línea de tiempo con los beats de la canción. Haz clic en un segmento
   para cambiar el clip, el punto de entrada, la duración, la transición, los efectos
   o los textos. *Ajustar al beat* lleva el final del segmento al beat más cercano.
   Sobre la línea de tiempo se ven las secciones de la canción y el drop.
   *Deshacer/Rehacer* (Ctrl+Z / Ctrl+Y). *Vista previa rápida* renderiza a media resolución;
   *Render final*, a calidad completa. Al terminar se muestra la **revisión de calidad**.

## Aprender estilos de tus videos

El editor analiza un video de referencia y detecta:

| Técnica | Cómo se detecta |
|---|---|
| Cortes secos y su relación con el ritmo | diferencia entre fotogramas + beats de la música |
| Pantalla dividida en paneles apilados y cuándo entra cada uno | brillo por franjas |
| Disolución / fundido cruzado | el fotograma intermedio es la mezcla de los extremos |
| Fundido a negro | rampa de brillo hasta negro |
| Flash blanco (como transición o dentro de un plano) | pico de brillo |
| Deslizamiento (slide) y su dirección | el contenido nuevo avanza desde un borde |
| Persona recortada que entra sobre el plano anterior | sólo cambia una parte de la imagen y avanza desde un borde |
| Glitch digital | dominante verde/ruido al final de un plano |
| Zoom de golpe | la imagen salta de escala de un fotograma a otro y luego vuelve |
| Temblor de cámara | sacudidas rápidas de ida y vuelta de la imagen |
| Cámara lenta / rampa de velocidad | el movimiento cae mucho dentro de un plano y luego se recupera |
| Textos sobreimpresos (y qué dicen) | zona fija y muy contrastada + OCR con tesseract |
| Pantalla dividida en **columnas** o en **cuadrícula** 2×2 (paneles que van apareciendo) | zonas negras de verdad que se van encendiendo, por columnas o por cuadrantes |
| **Imagen congelada** (freeze) | varios fotogramas idénticos (≥ 0.2 s) con movimiento antes y después; no se confunde con la repetición de fotogramas de una cámara lenta |
| **Estroboscopio** / parpadeo al ritmo, a negro o a blanco | al menos 3 pulsos de brillo regulares sobre la misma imagen; se guarda su ritmo |
| Planos en **blanco y negro** o **desaturados** | saturación de cada plano comparada con la del resto del video |
| **Destellos en blanco y negro** de unos fotogramas | el color desaparece un instante en mitad de un plano en color |
| **Zoom a través del corte** | el zoom acelera hasta el corte y el plano nuevo entra ampliado y se asienta (escala medida fotograma a fotograma) |
| **Barrido** (whip pan) y su dirección | desenfoque direccional a los dos lados del corte + desplazamiento muy rápido en un solo sentido |
| Barras de cine | filas siempre negras arriba y abajo |
| Cuánto movimiento tiene cada plano | energía de movimiento |
| Look de color | distribución de color (cuantiles RGB) |

Todo se guarda **en beats**, no en segundos. Así el estilo se adapta al tempo de
cualquier canción. Al aplicarlo:

- cada corte cae en el beat equivalente de la nueva canción (en los slides y en la
  persona que entra, lo que cae en el beat es el aterrizaje; en el zoom a través del
  corte y en el barrido, el propio corte);
- las congelaciones, los estroboscopios y los destellos en blanco y negro caen en el mismo
  beat del plano, y el ritmo del estroboscopio se guarda como subdivisión del beat
  (1/4, 1/3, 1/2…), así parpadea a tempo con cualquier canción;
- los planos en blanco y negro siguen en blanco y negro y los paneles conservan su
  distribución (franjas, columnas o cuadrícula);
- tus clips con más movimiento van donde la referencia tenía los planos más movidos
  (desactívalo con *Respetar mi orden*);
- si tienes más clips que la referencia, se repite el "cuerpo" del edit; si tienes
  menos, se reutilizan con otros tramos;
- el color de tus clips se ajusta con curvas hacia el de la referencia
  (*Copiar color*, de 0 a 1).

Desde la línea de comandos:

```bash
python -m videoeditor learn ref1.mov ref2.mov --name "Gym noche"
python -m videoeditor styles
python -m videoeditor auto --style "Gym noche" --music cancion.mp3 c1.mov c2.mov c3.mov c4.mov -o edit.mp4
python -m videoeditor analyze ref.mov --template plantilla.json   # informe + proyecto con los mismos tiempos
```

El estilo **Gym noche** viene incluido: es el que se aprendió del video de referencia original.

## LUTs de color

Un **LUT** es una tabla que cambia los colores de todo el video de golpe (un «filtro» de color
profesional). El editor trae **9 looks hechos para edits de gym de noche** (oscuros, con luces LED
azules/moradas, tungsteno cálido y ropa negra), los genera con `numpy` y los aplica con el filtro
`lut3d` de FFmpeg, así que no hacen más lento el render. Cada preset se compone de operaciones
simples (curva de contraste en la luminancia, negros levantados, caída de las luces, tintes en
sombras y luces, ajustes de saturación por tono y mezcla de canales) y **protege los tonos de piel**
en los looks donde importa. Probados sobre clips oscuros: ninguno aplasta la imagen a negro.

| Id | Nombre | Qué hace |
|---|---|---|
| `noche_azul` | Noche azul | El look de Gym noche: sombras azuladas, luces cálidas, contraste alto |
| `acero_frio` | Acero frío | Gris azulado desaturado y duro, como metal bajo luz fría |
| `oro_gym` | Oro gym | Luces doradas de tungsteno y sombras profundas, gimnasio de los 90 |
| `teal_orange` | Teal y naranja | El clásico de cine: sombras verde azuladas y piel naranja |
| `bleach_bypass` | Bleach bypass | Crudo y granuloso: poca saturación, contraste alto |
| `phonk_rojo` | Phonk rojo | Agresivo: negros aplastados, acentos rojos y magenta, azules hacia violeta |
| `mate_film` | Mate film | Negros levantados y color desvaído de película mate |
| `monocromo_contraste` | Monocromo contraste | Blanco y negro duro, negros profundos y luces limpias |
| `neon_morado` | Neón morado | Luces LED violeta y rosa; azules hacia violeta, negros morados |

Además hay dos formas de LUT **aprendido**:

- `style:<estilo>` (p. ej. `style:gym_noche`): el color de un estilo aprendido convertido en LUT. Lleva una
  toma «neutra típica» hacia la distribución de color (cuantiles RGB) del estilo: la luminancia con una curva
  monótona (copia el contraste en *stops*; el brillo medio sólo la sigue en parte para no apagar el material)
  y, en cada nivel de brillo, la dominante de color (sombras azuladas, luces cálidas…). En la web aparece como
  «Aprendido del estilo». Con un LUT aprendido no se usa además el «Copiar color» por curvas.
- `python -m videoeditor lut-learn referencia.mov -o mi_look.cube [--neutral mi_clip.mp4]`: un LUT `.cube`
  hecho directamente de un video de referencia; con `--neutral` lleva *tu* material hacia el de la referencia.

### Intensidad

`lut_strength` va de 0 (sin cambios) a 1 (el look completo) y mezcla identidad→look. La mezcla se guarda
ya calculada en una caché (`~/.cache/videoeditor/luts`), así que al renderizar sólo se aplica `lut3d`.

### En la interfaz web

- **Nuevo edit**: elige un LUT en la cuadrícula de miniaturas (se ven sobre tu primer clip elegido, o el
  primero de la biblioteca; «Sin LUT» y «Aprendido del estilo» incluidos) y ajusta la intensidad.
- **Editor → Proyecto**: LUT e intensidad de todo el edit. **Segmento**: «LUT propio» (otro LUT, «Sin LUT» o
  el del proyecto) e intensidad.
- **Biblioteca → LUTs**: sube tus propios `.cube`; aparecen junto a los presets en todos los selectores.

### En la línea de comandos

```bash
python -m videoeditor luts                              # lista presets, tus LUTs y los aprendidos
python -m videoeditor luts --export mis_luts/           # escribe todos los presets como .cube
python -m videoeditor lut-preview clip.mp4 -o hoja.jpg --at 1.5   # hoja de contacto con todos
python -m videoeditor lut-preview clip.mp4 --luts noche_azul,oro_gym -o comparar.jpg
python -m videoeditor auto --style "Gym noche" --music cancion.mp3 --lut noche_azul --lut-strength 0.8 c1.mov c2.mov
python -m videoeditor auto --style "Gym noche" --lut style ...   # el LUT aprendido del estilo
```

### En el proyecto JSON

```jsonc
{
  "lut": "noche_azul",        // preset, "style:<estilo>" o ruta a un .cube (como siempre)
  "lut_strength": 0.8,        // 0..1 (por defecto 1)
  "segments": [
    {"type": "clip", "src": "a.mov", "duration": 2.0},                          // usa el del proyecto
    {"type": "clip", "src": "b.mov", "duration": 2.0, "lut": "oro_gym", "lut_strength": 0.6},
    {"type": "clip", "src": "c.mov", "duration": 2.0, "lut": "none"}            // sin LUT (también null)
  ]
}
```

El LUT se aplica **a cada plano** (como el look de color), no a los segmentos negros/de color ni al fondo
de los paneles apilados. Un `"lut": "ruta.cube"` de proyectos anteriores sigue funcionando; las rutas
relativas se leen desde el JSON o desde la carpeta `luts/` de la carpeta de trabajo.

### Usarlos en otras apps (CapCut, Premiere, DaVinci Resolve…)

Los presets están en [`videoeditor/luts/`](videoeditor/luts) como `.cube` estándar 33×33×33 (Adobe/Resolve).
Impórtalos como LUT en tu editor, o exporta una copia con `python -m videoeditor luts --export carpeta/`.
Si cambias los looks en `videoeditor/luts.py`, regenera los archivos con `python -m videoeditor.luts`
(un test comprueba que los publicados coinciden con el código).

## Uso desde la línea de comandos

### Edit automático con plantilla fija

```bash
python -m videoeditor auto --music cancion.mp3 \
    banca.mov mancuernas.mov peso_muerto.mov sentadilla.mov dominadas.mov \
    -o mi_edit.mp4
```

Genera `mi_edit.mp4` y `mi_edit.json`. Edita el JSON y vuelve a renderizar:

```bash
python -m videoeditor render mi_edit.json --preview   # rápido, a media resolución
python -m videoeditor render mi_edit.json             # calidad final
```

Opciones útiles de `auto`:

- `--style NOMBRE` estilo aprendido, o `--template gym_stack|beat_cuts` (plantillas fijas)
- `--duration 15` duración máxima · `--music-start 32.5` empezar la canción en el drop
- `--keep-order` respetar el orden de los clips · `--color-strength 0.8`
- `--grade night_blue|teal_orange|moody|warm|cold|bw|desaturated|vivid|faded|film|none`
- `--lut ID` y `--lut-strength 0.8` LUT de color (ver [LUTs de color](#luts-de-color))
- `--cinematic` halo en las luces y grano · `--no-sfx` sin efectos de sonido
- `--no-music-follow` no adaptar a la canción · `--no-accents` sin zooms/flashes automáticos

### Otras utilidades

```bash
python -m videoeditor music cancion.mp3   # secciones, drop, energía y golpes
python -m videoeditor beats cancion.mp3   # BPM y tiempos de cada beat
python -m videoeditor list                # looks, efectos y transiciones disponibles
```

## Formato del proyecto (JSON)

Ver [`examples/gym_reference.json`](examples/gym_reference.json). Las rutas son
relativas al archivo JSON.

```jsonc
{
  "output": {"width": 1080, "height": 1920, "fps": 30, "crf": 18},
  "music":  {"src": "cancion.mp3", "start": 0, "volume": 1.0, "fade_out": 0.4},
  "grade":  "night_blue",            // look global (cada segmento puede tener el suyo)
  "lut":    "noche_azul",            // opcional: preset, "style:<estilo>" o ruta a un .cube (LUT 3D)
  "lut_strength": 0.8,               // 0..1; cada segmento puede tener su "lut" / "lut_strength" ("none" = sin LUT)
  "color_match": {"r": [...], "g": [...], "b": [...]},  // look aprendido (lo rellena un estilo)
  "color_strength": 0.8,
  "sound_design": true,              // efectos de sonido automáticos según el montaje
  "sfx": [{"type": "impact", "at": 3.9, "volume": 0.7}],  // y/o manuales: whoosh, impact, riser, hit, glitch
  "motion_blur": true,               // desenfoque de movimiento en slides/zooms
  "segments": [
    {"type": "black", "duration": 0.5},

    {"type": "stack", "duration": 3.0, "gap": 0,
     "layout": "rows",     // rows (franjas, de arriba a abajo), columns (de izquierda a derecha) o grid (2 columnas)
     "panels": [
      {"src": "a.mov", "in": 1.0, "appear": 0.0},   // "appear": segundo en que entra
      {"src": "b.mov", "in": 0.5, "appear": 1.0},
      {"src": "c.mov", "in": 0.0, "appear": 2.0}
    ]},

    {"type": "clip", "src": "d.mov",
     "in": 2.0,            // segundo del clip donde empieza
     "duration": 2.0,      // duración en el edit
     "speed": 1.0,         // 0.5 = cámara lenta
     "ramp": [[1.0, 0.3], [1.8, 1.0]],  // rampa: a x0.3 en el segundo 1.0 y vuelta a x1 en 1.8
     "zoom": 1.0,          // >1 recorta más
     "focus": [0.5, 0.5],  // punto del encuadre a conservar (x, y)
     "mirror": false, "reverse": false,
     "transition": {"type": "subject_drop", "duration": 0.4, "direction": "top"},  // entrada a este segmento
     "effects": [{"type": "glitch", "at": "end", "duration": 0.6}],
     "texts": [{"text": "PUSH DAY", "y": 0.12, "size": 110, "start": 0.2, "end": 2.5}]
    },

    {"type": "color", "color": "white", "duration": 0.2}
  ]
}
```

Las transiciones **no desplazan los tiempos**: cada segmento empieza exactamente
donde dice el JSON, así los cortes siguen cayendo en el beat.

### Efectos

| Efecto      | Parámetros                                       | Qué hace |
|-------------|--------------------------------------------------|----------|
| `glitch`    | `at` (`end`/`start`/segundos), `duration`, `intensity` | Glitch digital tipo datamosh, como el final de la referencia |
| `punch`     | `times` [s], `amount`, `decay`                   | Zoom de golpe que decae (ideal en los beats) |
| `shake`     | `at`, `duration`, `amount`, `speed`              | Temblor de cámara |
| `flash`     | `times` [s], `amount`, `decay`                   | Destello blanco |
| `rgb_split` | `amount`, `at`, `duration`                       | Separación de canales RGB |
| `fade_in` / `fade_out` | `duration`, `color`                   | Fundido |
| `halation`  | `amount`                                         | Halo rojizo en las luces fuertes (película) |
| `film_grain`| `amount`                                         | Grano de película |
| `stabilize` | —                                                | Estabiliza la cámara (vidstab, dos pasadas) |
| `freeze`    | `at` [s], `duration`                             | Imagen congelada: se detiene en ese fotograma y luego el plano sigue desde ahí (combínalo con `flash` o `punch` en el mismo instante) |
| `strobe`    | `at`, `duration`, `rate` (parpadeos/s), `amount` (0–1), `color` (`black`/`white`), `duty` | Estroboscopio: la imagen parpadea a ritmo fijo |
| `bw_flash`  | `times` [s], `duration`                          | Unos fotogramas en blanco y negro (p. ej. en los beats) |
| `blur`, `grain`, `sharpen`, `letterbox` | `amount` / `size`        | Varios |

### Transiciones

`cut`, `fade`, `fadeblack`, `flash` (alias `fadewhite`), `slideleft/right/up/down`,
`wipeleft/right/up/down`, `smoothleft/right/up/down`, `zoom` (alias `zoomin`), `pixelize`,
`dissolve`, `circleopen`, `radial`, `squeezeh`, `squeezev`, `hblur`,
`subject_drop` (la persona del plano siguiente, recortada del fondo, entra sobre el
plano actual; `direction`: `top`, `bottom`, `left` o `right`) y dos transiciones
**centradas en el corte** (la mitad de su duración va al final del plano anterior y la
otra mitad al principio del siguiente, así el corte sigue cayendo en el beat):

- `zoomthrough`: zoom que acelera hacia el centro hasta el corte; el plano nuevo entra
  ampliado y se asienta.
- `whip`: barrido de cámara; la imagen sale disparada hacia `direction` (`left`, `right`,
  `top` o `bottom`; por defecto `left`) con desenfoque direccional y el plano nuevo llega
  igual de rápido. Con `sound_design` lleva un whoosh.

### Otras claves del proyecto

| Clave | Para qué |
|---|---|
| `output.preset` | velocidad de codificación de x264 (`ultrafast` … `veryslow`; por defecto `medium`) |
| `music.fade_in`, `music.normalize` | fundido de entrada; normalizar a -14 LUFS (por defecto `true`) |
| `sfx_volume` | volumen de todos los efectos de sonido (por defecto 1) |
| `info.beats`, `info.drop` | beats y drop de la canción (los rellena la app; la revisión de calidad los usa) |
| textos: `x`, `color`, `font`, `box` | posición horizontal (0-1), color, fuente y caja de fondo |
| clip: `ramp_ease` | suavizado de cada cambio de velocidad de la rampa (s) |
| segmento: `grade`, `lut`, `lut_strength` | look propio de un plano |

### Variables de entorno

`VIDEOEDITOR_WORKSPACE` (carpeta de trabajo por defecto), `VIDEOEDITOR_CACHE` (cachés),
`VIDEOEDITOR_LUT_CACHE` (LUTs con intensidad) y `VIDEOEDITOR_MODELS` (modelo de MediaPipe).

## Calidad: sin fallos

Cada render pasa por una revisión automática:

- **Antes**: si un clip es más corto que su hueco, mueve el punto de entrada o lo
  ralentiza un poco en vez de congelar la imagen; los clips **HDR del iPhone** se
  convierten a SDR con mapeo de tonos (si no, salen lavados); la música se normaliza
  a **-14 LUFS** (volumen estándar de redes) y un limitador evita que sature.
- **Después**: avisa de imágenes congeladas o negros no previstos (las congelaciones del
  efecto `freeze` y los parpadeos del estroboscopio sí están previstos), cortes fuera del
  beat, audio saturado o con volumen raro y diferencias de duración.
- El punto de entrada automático de cada clip busca la repetición con más movimiento
  y descarta tramos borrosos o de cámara moviéndose.

## Estructura del código

```
videoeditor/
  ff.py        llamadas a ffmpeg/ffprobe, escapado de rutas en filtros
  sysutil.py   carpetas de caché por sistema, reemplazo seguro de archivos
  doctor.py    comprobación de la instalación (python -m videoeditor doctor)
  project.py   modelo y validación del JSON
  render.py    render por segmentos + unión con concat/xfade + música y sonido
  effects.py   looks de color y efectos (filtros de FFmpeg)
  luts.py      LUTs 3D (.cube): presets, aprendidos, intensidad, caché y hojas de contacto
  luts/        los presets como archivos .cube (para CapCut, Premiere, DaVinci)
  beats.py     tempo y beats (búsqueda global de la rejilla)
  music.py     mapa musical: secciones, drop, energía, golpes
  auto.py      plantillas fijas y elección del mejor tramo de cada clip
  analyze.py   análisis de videos de referencia (cortes, transiciones, efectos, color)
  motionfx.py  zooms de golpe, temblores, cámara lenta y textos
  style.py     aprender estilos y aplicarlos guiado por la música
  sfx.py       efectos de sonido sintetizados y su colocación
  qa.py        revisión de calidad antes y después del render
  segment.py   recorte de personas con MediaPipe
  server.py    servidor de la interfaz web
  web/         interfaz web (HTML/CSS/JS sin dependencias)
  styles/      estilos incluidos
  __main__.py  línea de comandos
tests/         tests de extremo a extremo con clips y canciones sintéticas
```

Tests: `python -m unittest discover -s tests -v`

## Limitaciones

- La cámara lenta sólo se reconoce cuando hay velocidad normal antes y después (rampa);
  un plano entero a cámara lenta no se distingue de un movimiento lento.
- Con textos fijos encima, los temblores de cámara pequeños pueden pasar desapercibidos.
- Las secciones de la canción se detectan bien en música con estructura clara (pop, EDM,
  trap, phonk); en canciones muy uniformes todo cuenta como una sola sección.
- El recorte de personas funciona mejor con una sola persona y buena iluminación. En
  planos muy oscuros el borde puede quedar irregular.
- Sólo suena la música; el audio original de los clips no se mezcla.
- Un plano nocturno muy oscuro con la parte de arriba completamente negra puede
  confundirse con paneles apilados (y entonces no se mira si va en blanco y negro).
- El barrido se reconoce por el desenfoque direccional y el desplazamiento rapidísimo a
  los dos lados del corte; un barrido real sin apenas desenfoque, o uno hacia arriba o
  abajo sobre imágenes casi sin detalle vertical, puede quedarse en un corte seco.
- La congelación necesita movimiento antes y después: en un plano casi fijo no se
  distingue de la propia imagen quieta. El "color pop" (sólo un objeto en color) no se
  detecta: cuenta como plano en color.
