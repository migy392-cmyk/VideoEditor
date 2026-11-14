# Hoja de ruta

| Fase | Qué se hace | Responsable | Terminado cuando… |
|---|---|---|---|
| 0 | Estructura de `.md`, explicación al usuario, preguntas | director | El usuario da el visto bueno ← **estamos aquí** |
| 1 | Salida a DaVinci mínima: cortes + música + marcadores | ingeniero-davinci | El usuario importa un edit de prueba en **su** Resolve y los cortes caen en los golpes |
| 1b | Velocidades, zoom, fundidos, script Lua (LUT, notas), modo Fiel | ingeniero-davinci | La línea de tiempo en Resolve se ve igual que `vista_previa.mp4` |
| 2 | Aprendizaje mejorado: capa visual, confianza por técnica, ficha en español | analista-estilos | Con 2–3 referencias reales del usuario, la ficha describe lo que él ve |
| 3 | Catálogo de clips y montaje guiado; `auto --davinci` | montador | Un solo comando: estilo + clips + canción → carpeta de entrega |
| 4 | Revisión de calidad ampliada (también del FCPXML) y ciclo de correcciones | revisor | "Cambia el plano 3" se resuelve regenerando sin romper nada |
| 5 | Guías, instalador actualizado, `CLAUDE.md` | documentador | El usuario lo hace solo siguiendo `docs/GUIA_PC.md` |

La Fase 1 va primero a propósito: es la única con riesgo real (que la versión del usuario
importe bien el archivo). Si falla, se cambia de formato antes de construir encima.
