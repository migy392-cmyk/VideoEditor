# Arquitectura

## Principio
No se empieza de cero. El paquete `videoeditor/` ya analiza referencias, aprende estilos,
analiza música, monta y renderiza con FFmpeg. Lo nuevo es:
1. un aprendizaje **más rico** (ojo del director sobre fotogramas + detectores),
2. una **salida a DaVinci Resolve gratis**,
3. un **flujo dirigido** por agentes con fichas en `.md`.

## Flujo de datos
```
referencias ──analyze/learn──► estilo.json + ficha.md ─┐
música ──────music───────────► estructura musical ─────┼─► style (aplicar) ─► proyecto.json
clips ───────catálogo de clips (mejores tramos) ───────┘                          │
                                                          ┌───────────────────────┤
                                                          ▼                       ▼
                                             render.py (vista previa)    davinci/ (NUEVO)
                                                                         ├─ fcpxml.py   línea de tiempo
                                                                         ├─ bake.py     planos con efecto
                                                                         ├─ script.py   script Lua para Resolve
                                                                         └─ paquete.py  carpeta de entrega
```
El **proyecto JSON** sigue siendo la única fuente de verdad: la vista previa de FFmpeg y la
entrega a DaVinci salen del mismo archivo, así nunca se contradicen.

## Módulos nuevos (previstos)
| Módulo | Función |
|---|---|
| `videoeditor/davinci/fcpxml.py` | Proyecto JSON → FCPXML (cortes, in/out, velocidad, zoom básico, fundidos, audio, marcadores en beats) |
| `videoeditor/davinci/bake.py` | Decide qué planos no son traducibles y los pre-renderiza a un intermedio de calidad |
| `videoeditor/davinci/script.py` | Genera un script **Lua** (no exige instalar Python para Resolve): LUT, marcadores con notas, ajustes de proyecto |
| `videoeditor/davinci/paquete.py` | Monta `workspace/davinci/<nombre>/` con todo + `LEEME.md` |
| `videoeditor/catalogo.py` | Ficha de cada clip del usuario: mejores tramos, movimiento, brillo, etiquetas del director |
| `videoeditor/ficha.py` | Estilo aprendido → ficha en español (`workspace/styles/<estilo>.md`) |

Comandos nuevos: `python -m videoeditor davinci proyecto.json [--modo fiel|editable]`
y `auto ... --davinci` para hacerlo todo seguido.

## Mejora del aprendizaje
- **Capa numérica** (ya existe): cortes, beats, transiciones, efectos, color.
- **Capa visual del director** (nueva): el agente analista mira hojas de contacto de cada
  plano y añade al estilo lo que el código no saca (tipo de plano, encuadre, sujeto,
  intención). Se guarda en el estilo como `anotaciones`, opcionales: el código funciona sin ellas.
- **Confianza por técnica**: con varias referencias del mismo estilo, cada técnica lleva en
  cuántas aparece, para no copiar una rareza de un solo video.

## Restricciones que se mantienen
- Dependencias: numpy + FFmpeg. El FCPXML se escribe a mano (XML estándar), sin librerías nuevas.
- Textos y mensajes en español. Rutas Windows con `ff.filter_value()`.
- Cada técnica nueva lleva test de ida y vuelta. El FCPXML lleva tests de estructura
  (tiempos exactos por fotograma, sin huecos ni solapes).
