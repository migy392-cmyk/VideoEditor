# Equipo de agentes

El **director** (sesión principal) decide, reparte, integra y revisa visualmente. No delega
la decisión final ni la comunicación con el usuario.

| Agente | Modelo | Por qué ese modelo | Territorio (archivos) |
|---|---|---|---|
| `analista-estilos` | opus | Tiene que mirar fotogramas y razonar sobre técnica de montaje | `analyze.py`, `motionfx.py`, aprendizaje en `style.py`, `ficha.py` |
| `ingeniero-davinci` | opus | Formato delicado (FCPXML, tiempos exactos, scripts de Resolve): un error rompe la importación | `videoeditor/davinci/*`, `tests/test_davinci*.py` |
| `montador` | sonnet | Trabajo acotado sobre código existente | `auto.py`, aplicación en `style.py`, `catalogo.py`, `__main__.py` |
| `revisor` | sonnet | Revisión independiente: tests, calidad, fotogramas; no escribe funciones nuevas | `qa.py`, `tests/` |
| `documentador` | haiku | Guías en español a partir de lo ya hecho | `docs/*.md`, `README.md`, `LEEME.md` de entrega |

## Reglas de trabajo
1. Cada agente trabaja en una copia aislada del proyecto y solo en su territorio; si necesita
   tocar fuera, lo pide al director.
2. Cada encargo lleva: objetivo, archivos, criterio de "terminado" y qué prueba lo demuestra.
3. Nadie se aprueba a sí mismo: lo que hace un agente lo comprueba `revisor` y lo mira el director.
4. Todo en español. Solo numpy + FFmpeg.
5. Tests completos en verde antes de integrar (`python -m unittest discover -s tests`).
6. Las dudas de producto las resuelve el director y las apunta en `DECISIONES.md`; al usuario
   solo se le pregunta lo que depende de su gusto o de su PC.

## Orden y paralelismo
- En paralelo: `ingeniero-davinci` (Fase 1) y `analista-estilos` (Fase 2): no comparten archivos.
- Después: `montador` (Fase 3), que usa lo de ambos.
- `revisor` tras cada fase; `documentador` al final de cada fase.
