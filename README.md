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
pip install -r requirements.txt   # sólo numpy
```

## Uso rápido

### 1. Edit automático (lo más fácil)

Pasa tus clips y una canción. Los 3 primeros clips van a los paneles; el resto,
a pantalla completa. El programa detecta el tempo, coloca cada corte en un beat
y elige el tramo de cada clip con más movimiento.

```bash
python -m videoeditor auto --music cancion.mp3 \
    banca.mov mancuernas.mov peso_muerto.mov sentadilla.mov dominadas.mov \
    -o mi_edit.mp4
```

Esto genera `mi_edit.mp4` **y** `mi_edit.json`. Si algo no te gusta, edita el
JSON (puntos de entrada, duraciones, efectos, textos…) y vuelve a renderizar:

```bash
python -m videoeditor render mi_edit.json --preview   # rápido, a media resolución
python -m videoeditor render mi_edit.json             # calidad final
```

Opciones útiles de `auto`:

- `--template gym_stack` (por defecto, el estilo de la referencia) o `beat_cuts` (cortes rápidos cada 2 beats con zoom de golpe)
- `--duration 15` duración máxima
- `--music-start 32.5` empezar la canción en el drop
- `--grade night_blue|teal_orange|moody|warm|cold|bw|vivid|faded|none`
- `--width 1080 --height 1920 --fps 30`

### 2. Copiar la estructura de cualquier video

```bash
python -m videoeditor analyze referencia.mov --template plantilla.json
```

Muestra los cortes, paneles, glitch y su relación con los beats, y guarda un
proyecto con **los mismos tiempos** donde sólo tienes que cambiar `clipN.mov`
por tus archivos.

### 3. Otras utilidades

```bash
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
     "zoom": 1.0,          // >1 recorta más
     "focus": [0.5, 0.5],  // punto del encuadre a conservar (x, y)
     "mirror": false, "reverse": false,
     "transition": {"type": "slidedown", "duration": 0.4},  // entrada a este segmento
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
| `blur`, `grain`, `sharpen`, `letterbox` | `amount` / `size`        | Varios |

### Transiciones

`cut`, `fade`, `fadeblack`, `flash`, `slideleft/right/up/down`,
`wipeleft/right/up/down`, `smoothleft/right/up/down`, `zoom`, `pixelize`,
`dissolve`, `circleopen`, `radial`, `squeezeh`, `squeezev`, `hblur`.

## Estructura del código

```
videoeditor/
  project.py   modelo y validación del JSON
  render.py    render por segmentos + unión con concat/xfade + música
  effects.py   looks de color y efectos (filtros de FFmpeg)
  beats.py     detección de tempo y beats (numpy)
  auto.py      plantillas automáticas y elección del mejor tramo de cada clip
  analyze.py   análisis de videos de referencia
  __main__.py  línea de comandos
tests/         tests de extremo a extremo con clips sintéticos
```

Tests: `python -m unittest discover -s tests -v`

## Limitaciones y próximos pasos

- En la referencia, en la entrada al primer plano completo la **silueta de la persona
  (recortada del fondo)** cae sobre los paneles. Aquí se aproxima con una transición
  `slidedown` del plano entero. Para hacerlo igual hace falta segmentación de personas
  (p. ej. MediaPipe o rembg).
- De momento sólo se usa la música; el audio original de los clips no se mezcla.
- No hay interfaz gráfica todavía: el siguiente paso natural es una interfaz web con
  línea de tiempo que lea y escriba este mismo JSON.
