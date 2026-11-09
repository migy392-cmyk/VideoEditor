# Cómo enseñarle a la app técnicas nuevas con tus videos

La app aprende en dos niveles:

1. **Lo hace ella sola (sin programar):** le pasas un video editado que te guste y aprende
   su *estilo*: el ritmo de los cortes, qué técnicas usa, en qué momento de la canción y el color.
2. **Técnicas que todavía no conoce:** si el video usa algo que la app nunca ha visto, no lo puede
   inventar. Ahí hace falta añadir un "detector" nuevo al código (más abajo te explico cómo pedirlo).

---

## 1. Enseñarle un estilo con tus videos (lo normal)

### Paso a paso (interfaz web)

1. Abre la app (`python -m videoeditor serve`) y ve a **Biblioteca → Referencias**.
2. Arrastra el video que te gusta (el archivo original, con su música).
3. Ve a **Estilos**, marca ese video, escribe un nombre (por ejemplo *Gym phonk*) y pulsa **Aprender**.
4. Aparece la **receta** del estilo: una barra con cada plano medido en *beats*, las transiciones
   (rayado) y los efectos (✦). Pasa el ratón por encima de cada bloque para ver los detalles.
5. Para que aprenda mejor, **repite el paso 3 con más videos del mismo estilo usando el mismo nombre**:
   cada video se guarda como una *variante* y el color se promedia. Con 3–5 videos el resultado es
   mucho más fiable que con uno.
6. En **Nuevo edit** elige ese estilo, tu canción y tus clips. La app hará un video con ese estilo,
   adaptado a tu canción (drop, energía, golpes).

### Desde la línea de comandos

```bash
python -m videoeditor learn ref1.mp4 ref2.mp4 ref3.mp4 --name "Gym phonk"
python -m videoeditor styles                        # ver los estilos aprendidos
python -m videoeditor analyze ref1.mp4              # ver qué ha detectado, plano a plano
```

### Qué técnicas reconoce hoy

| Tipo | Técnicas |
|---|---|
| Estructura | cortes secos al ritmo, negro inicial, paneles apilados en filas, columnas o cuadrícula (y cuándo entra cada panel) |
| Transiciones | corte, disolución, fundido a negro, flash, deslizamiento (4 direcciones), persona recortada que entra, zoom a través del corte, barrido (whip) |
| Efectos dentro de un plano | glitch, zoom de golpe, temblor, cámara lenta / rampa de velocidad, imagen congelada, estroboscopio, destello en blanco y negro, flash, textos (y qué dicen) |
| Look | planos en blanco y negro o desaturados, barras de cine, color general (para el LUT aprendido) |
| Ritmo | duración de cada plano en beats, cuánto movimiento tiene cada plano, energía de la música en cada momento |

### Consejos para que aprenda bien

- **Usa el archivo original**, no una grabación de pantalla: la compresión fuerte o los marcos de la app
  (botones, textos de TikTok) confunden el análisis.
- **El video debe tener su música**: los tiempos se miden en beats. Sin audio, la app supone 120 BPM.
- **Un estilo = un tipo de edit.** No mezcles en el mismo nombre un edit lento y cinematográfico con
  uno rápido de phonk; crea dos estilos.
- **Revisa lo que ha entendido** con `analyze` (o la barra de la receta). Si algo no cuadra, ese video
  quizá no sea buena referencia.

---

## 2. Técnicas que la app todavía no conoce

Si en tu referencia hay algo que **no aparece en el informe de `analyze`** (por ejemplo una transición
tipo cubo 3D, un efecto de espejo caleidoscopio o un texto que se escribe letra a letra), la app lo
verá como un corte normal o no lo verá.

Para añadirla, pásame (a Claude) el video y dime **en qué segundo** aparece y **qué es**. Por ejemplo:

> "En el segundo 4,2 de este video hay una transición en la que la imagen gira como un cubo.
> Quiero que la app la aprenda."

Lo que hago con esa petición (es lo mismo que se hizo con las últimas 6 técnicas):

1. **Analizo ese momento** fotograma a fotograma para ver cómo está construida la técnica.
2. **Añado el render**: la app aprende a *hacer* la técnica (un efecto o transición nueva en el JSON).
3. **Añado el detector**: la app aprende a *reconocerla* en cualquier video.
4. **Pruebo ida y vuelta**: genero videos con la técnica, compruebo que el detector la encuentra en el
   segundo exacto, y que **no** la ve en videos que no la tienen (tus clips reales incluidos).
5. **La conecto a los estilos**: a partir de ahí, cualquier estilo que aprendas con videos que usen esa
   técnica la reproducirá en tus edits, sincronizada con la música.

Cuantos más ejemplos de la técnica me pases (2–3 videos distintos), más fiable será el detector.
