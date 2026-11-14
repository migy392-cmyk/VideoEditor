# Entrega a DaVinci Resolve (versión gratis)

## Lo que condiciona el diseño
- Resolve **gratis** no admite control externo por script (solo la versión Studio). Sí
  admite importar líneas de tiempo (FCPXML / EDL / AAF…) y ejecutar scripts **desde dentro**
  (*Workspace → Scripts* y la consola).
  → **Hay que verificarlo en el PC del usuario en la Fase 1**, con su versión exacta.
- Por eso se descartan los MCP de terceros para Resolve: dependen del control externo.
- Resolve gratis en Windows lee H.264 de 8 bits y exporta hasta UHD; suficiente para Reels.

## Qué viaja y cómo
| Elemento del edit | Vía | Editable en Resolve |
|---|---|---|
| Cortes, orden, in/out | FCPXML | Sí |
| Música y su punto de inicio | FCPXML | Sí |
| Velocidad constante (cámara lenta/rápida) | FCPXML | Sí |
| Rampas de velocidad | FCPXML troceado en tramos, o plano procesado | Parcial |
| Zoom / posición fijos | FCPXML | Sí |
| Fundido encadenado | FCPXML | Sí |
| Marcadores de beat, drop y notas | FCPXML + script | Sí |
| LUT | Script Lua (o a mano: clic derecho → LUT) | Sí |
| Sacudida, destello, RGB, halo, congelado con recorte, transiciones especiales | Plano procesado por FFmpeg (modo Fiel) | No (original en pista oculta) |
| Efectos de sonido | Archivos WAV en pista de audio propia | Sí |

## Formato de trabajo
- Línea de tiempo vertical 1080×1920, a los fps del material del usuario (se detecta; por defecto 30).
- Todos los tiempos en fracciones exactas de fotograma: el corte cae en el fotograma más
  cercano al beat.
- Planos procesados: intermedio de alta calidad. Códec a decidir en la Fase 1 según lo que
  lea bien la versión del usuario (candidato DNxHR HQ en `.mov`; alternativa H.264 de 8 bits
  con calidad muy alta).
- Rutas absolutas del PC del usuario dentro del FCPXML; si se mueve la carpeta, se regenera.

## Carpeta de entrega
```
workspace\davinci\<nombre>\
  <nombre>.fcpxml        ← importar en Resolve
  <nombre>.lua           ← script para el menú Workspace → Scripts
  media\                 ← planos procesados y efectos de sonido
  luts\<lut>.cube
  vista_previa.mp4
  LEEME.md               ← los 3 pasos, en español
```
