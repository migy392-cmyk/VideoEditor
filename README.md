# VideoEditor

Editor de video por proyectos JSON, basado en FFmpeg, para hacer edits verticales
de gym / fitness sincronizados con la música, del estilo del video de referencia.

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

Hace falta **Python 3.10+** y **FFmpeg** (`ffmpeg` y `ffprobe` en el PATH).

```bash
pip install -r requirements.txt
```

`mediapipe` es opcional: sólo se usa para recortar a la persona (transición
`subject_drop`). El modelo (~16 MB) se descarga solo la primera vez. Sin él, esa
transición se convierte en un deslizamiento normal. En Linux, MediaPipe necesita
además `libegl1` y `libgles2` (`apt-get install libegl1 libgles2`).

`tesseract` (opcional, `apt-get install tesseract-ocr tesseract-ocr-spa`) permite leer
los textos de los videos de referencia. FFmpeg debe incluir `zscale` (HDR) y `vidstab`
(estabilización); las versiones de Ubuntu/Debian y Homebrew los traen.

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
python -m videoeditor serve          # abre http://127.0.0.1:8765
```

Todo se guarda en la carpeta `workspace/` (cámbiala con `-w otra_carpeta`).

1. **Biblioteca**: sube tus clips, canciones y **videos de referencia** (edits que te gustan).
2. **Estilos**: elige referencias, ponle un nombre y pulsa *Aprender*. Verás la
   "receta" del estilo: planos, paneles, transiciones y efectos medidos en beats.
   Si aprendes con un nombre que ya existe, el video se añade como otra variante
   del mismo estilo y el color se promedia.
3. **Nuevo edit**: elige estilo, canción y clips (en orden), y pulsa *Crear edit*. Verás el
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
| Barras de cine | filas siempre negras arriba y abajo |
| Cuánto movimiento tiene cada plano | energía de movimiento |
| Look de color | distribución de color (cuantiles RGB) |

Todo se guarda **en beats**, no en segundos. Así el estilo se adapta al tempo de
cualquier canción. Al aplicarlo:

- cada corte cae en el beat equivalente de la nueva canción (en los slides y en la
  persona que entra, lo que cae en el beat es el aterrizaje);
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
- `--grade night_blue|teal_orange|moody|warm|cold|bw|vivid|faded|film|none`
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
  "lut":    "mi_look.cube",          // opcional, LUT 3D
  "color_match": {"r": [...], "g": [...], "b": [...]},  // look aprendido (lo rellena un estilo)
  "color_strength": 0.8,
  "sound_design": true,              // efectos de sonido automáticos según el montaje
  "sfx": [{"type": "impact", "at": 3.9, "volume": 0.7}],  // y/o manuales: whoosh, impact, riser, hit, glitch
  "motion_blur": true,               // desenfoque de movimiento en slides/zooms
  "segments": [
    {"type": "black", "duration": 0.5},

    {"type": "stack", "duration": 3.0, "gap": 0, "panels": [
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
| `blur`, `grain`, `sharpen`, `letterbox` | `amount` / `size`        | Varios |

### Transiciones

`cut`, `fade`, `fadeblack`, `flash`, `slideleft/right/up/down`,
`wipeleft/right/up/down`, `smoothleft/right/up/down`, `zoom`, `pixelize`,
`dissolve`, `circleopen`, `radial`, `squeezeh`, `squeezev`, `hblur` y
`subject_drop` (la persona del plano siguiente, recortada del fondo, entra sobre el
plano actual; `direction`: `top`, `bottom`, `left` o `right`).

## Calidad: sin fallos

Cada render pasa por una revisión automática:

- **Antes**: si un clip es más corto que su hueco, mueve el punto de entrada o lo
  ralentiza un poco en vez de congelar la imagen; los clips **HDR del iPhone** se
  convierten a SDR con mapeo de tonos (si no, salen lavados); la música se normaliza
  a **-14 LUFS** (volumen estándar de redes) y un limitador evita que sature.
- **Después**: avisa de imágenes congeladas o negros no previstos, cortes fuera del
  beat, audio saturado o con volumen raro y diferencias de duración.
- El punto de entrada automático de cada clip busca la repetición con más movimiento
  y descarta tramos borrosos o de cámara moviéndose.

## Estructura del código

```
videoeditor/
  project.py   modelo y validación del JSON
  render.py    render por segmentos + unión con concat/xfade + música y sonido
  effects.py   looks de color y efectos (filtros de FFmpeg)
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
