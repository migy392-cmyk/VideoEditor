# Director de edits automáticos — índice

Esta carpeta es el **plan de trabajo** del director (Claude) y de su equipo de subagentes.
Nada de lo que hay aquí es código: es lo que se decide **antes** de programar.

| Documento | Para qué sirve |
|---|---|
| `COMO_FUNCIONA.md` | Explicación para el usuario, sin jerga: qué hará la app, paso a paso |
| `ARQUITECTURA.md` | Piezas técnicas: qué se reutiliza del editor actual y qué se añade |
| `DAVINCI.md` | Cómo se entrega el edit a DaVinci Resolve **gratis**: qué pasa nativo y qué no |
| `EQUIPO.md` | Subagentes, modelo de cada uno, qué archivos toca cada uno y cómo se revisan |
| `HOJA_DE_RUTA.md` | Fases de trabajo, en orden, con criterio de "terminado" |
| `DECISIONES.md` | Decisiones ya tomadas por el director y preguntas abiertas al usuario |

Las definiciones de los subagentes están en `.claude/agents/`.

Regla de oro: **el director no da nada por terminado sin verlo** (fotogramas extraídos,
línea de tiempo abierta en DaVinci por el usuario, tests en verde).
