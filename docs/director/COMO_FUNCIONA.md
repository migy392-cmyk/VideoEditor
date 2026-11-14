# Cómo va a funcionar (explicado sin jerga)

## La idea en una frase
Tú me das **videos de muestra** que te gustan, yo aprendo **cómo están editados**, y después
monto **tus clips** con ese mismo estilo y te lo dejo listo para abrir en **DaVinci Resolve**
como una línea de tiempo normal, que puedes retocar a mano y exportar.

## Paso a paso

### 1. Enseñar (videos de muestra)
Dejas los videos en `workspace\references\`. De cada uno saco:
- **Cortes**: en qué segundo cambia cada plano y cuánto dura.
- **Ritmo**: si los cortes caen en los golpes de la música (cada golpe, cada 2, en el drop…).
- **Técnicas**: cámara lenta/rápida, rampas de velocidad, zooms, sacudidas, destellos,
  transiciones, congelados, textos.
- **Color**: tono general, contraste, saturación (para elegir o crear un LUT parecido).
- **Estructura**: intro tranquila → subida → drop → cierre.

Además del análisis automático por números, **yo miro fotogramas** del video para confirmar
lo que el programa detecta y describir lo que el programa no ve (tipo de plano, encuadre,
estética). Lo aprendido se guarda como un **estilo con nombre** en `workspace\styles\`, con
una ficha en español que puedes leer.

### 2. Preparar (tus clips y tu canción)
Dejas tus tomas en `workspace\clips\` y la canción en `workspace\music\`. Yo:
- Analizo la canción (tempo, secciones, drop, golpes).
- Reviso cada clip y busco **los mejores tramos** (movimiento, nitidez, momento del esfuerzo).

### 3. Montar (el plan del edit)
Aplico el estilo a tu material: qué tramo de qué clip va en cada hueco, con qué velocidad y
qué técnica, todo cuadrado con los golpes de la música. Esto es un **plan** (un archivo de
proyecto) que te resumo en español.

### 4. Entregar en DaVinci
Te dejo en `workspace\davinci\<nombre>\`:
- Un archivo de **línea de tiempo** (`.fcpxml`) que importas en DaVinci con
  *Archivo → Importar → Línea de tiempo*. Aparecen tus clips cortados, la música, las
  velocidades y **marcadores en cada golpe**.
- Un **script para DaVinci** (menú *Workspace → Scripts*) que hace lo que el archivo no
  puede: aplicar el LUT y poner notas ("aquí va destello", "aquí sacudida").
- Los **planos con efecto ya hecho** cuando DaVinci gratis no puede recrearlo solo
  (ver "dos modos").
- Una **vista previa** en `.mp4` para ver el resultado antes de abrir DaVinci.

### 5. Revisar y corregir
Antes de entregarte nada paso una revisión de calidad (cortes fuera de golpe, planos
congelados, audio). Tú lo ves y me dices "más rápido el principio", "quita los destellos",
"cambia el plano 4": corrijo el plan y lo vuelvo a generar.

## Dos modos de entrega
| Modo | Qué recibes en DaVinci | Cuándo usarlo |
|---|---|---|
| **Fiel** (por defecto) | Tus clips cortados y con velocidad; los planos con efectos especiales (sacudida, destello, RGB…) vienen ya procesados. El original queda en una pista oculta para poder volver atrás | Quieres que se vea como la muestra sin tocar nada |
| **Editable** | Solo tus clips originales cortados y con velocidad; los efectos van como marcadores con instrucciones | Quieres poner tú los efectos a mano |

## Cómo me lo pides
Hablándome normal en Claude Code, en la carpeta del proyecto:
- "Aprende el estilo de los videos de referencias y llámalo *Phonk oscuro*."
- "Monta un edit de 15 segundos con mis clips y la canción, estilo *Phonk oscuro*, y pásamelo a DaVinci."
- "El plano 3 no me gusta, pon otro."

## Límites (para que no haya sorpresas)
- DaVinci **gratis** no deja que un programa externo lo maneje a distancia (eso es de la
  versión de pago). Por eso hay un paso manual: **importar el archivo** y lanzar el script
  desde el menú. Son dos clics.
- Se copia **la forma de editar** de la muestra, no su contenido.
- Efectos muy artesanales (recortes complejos, 3D, textos animados a medida) se aproximan o
  se dejan indicados con un marcador.
