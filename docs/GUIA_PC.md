# Guía: usar VideoEditor en tu PC

Esta guía es para usarlo en tu ordenador, paso a paso. Tardarás unos 15 minutos la primera vez.

> Los pasos de Linux están probados. Los de Windows y macOS están revisados en el código y con
> pruebas que simulan esos sistemas, pero no se han ejecutado en un PC real con Windows o Mac.
> Si algo falla, ejecuta `python -m videoeditor doctor` (paso 4): te dice qué falta y cómo arreglarlo.

---

## Instalación rápida en Windows (un solo paso)

1. Abre **PowerShell** (botón Inicio → escribe "PowerShell" → Enter). No hace falta "como administrador".
2. Copia y pega esta línea y pulsa Enter:

   ```powershell
   irm https://raw.githubusercontent.com/migy392-cmyk/VideoEditor/claude/serene-brown-fggcdl/scripts/instalar_windows.ps1 | iex
   ```

3. Espera (10–20 minutos la primera vez). Si Windows pregunta *"¿Desea permitir…?"*, pulsa **Sí**
   (a veces la ventana aparece detrás de PowerShell).

El instalador instala Python, Git y FFmpeg, descarga el proyecto en **`C:\VideoEditor`**, prepara todo,
comprueba la instalación y crea un acceso directo **VideoEditor** en el escritorio. Se puede volver a
ejecutar cuando quieras: lo ya instalado se salta y el proyecto se actualiza a la última versión.

> Si prefieres no pegar comandos: descarga el ZIP del proyecto desde GitHub (**Code → Download ZIP**),
> descomprímelo (clic derecho → *Extraer todo*) y haz doble clic en **`INSTALAR_WINDOWS.bat`**.

Después, para usarlo, haz doble clic en **VideoEditor** en el escritorio y salta al [paso 5](#5-úsalo-cada-día).
Los pasos 1–4 de abajo son la instalación manual (para macOS, Linux o si el instalador falla).

---

## 1. Instala los programas necesarios (solo la primera vez)

Necesitas **Python 3.10 o superior** y **FFmpeg** (la versión "full").

### Windows 10/11
Abre **PowerShell** (botón Inicio → escribe "PowerShell") y ejecuta:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install Git.Git
```

Cierra PowerShell y ábrelo de nuevo (para que reconozca los programas nuevos).

> Si no tienes `winget`: descarga Python de python.org (marca **"Add Python to PATH"** al instalar) y
> FFmpeg *full* de gyan.dev (`ffmpeg-release-full.7z`), descomprímelo en `C:\ffmpeg` y añade
> `C:\ffmpeg\bin` al PATH (Inicio → "variables de entorno").

### macOS
Instala Homebrew (brew.sh) y después, en la app Terminal:

```bash
brew install python ffmpeg git
```

### Linux (Ubuntu/Debian)
```bash
sudo apt install python3 python3-venv ffmpeg git
```

---

## 2. Descarga el editor

El código está en GitHub, en la rama `claude/serene-brown-fggcdl` del repositorio
`migy392-cmyk/VideoEditor`. Elige una carpeta (por ejemplo tus Documentos) y ejecuta:

```bash
git clone -b claude/serene-brown-fggcdl https://github.com/migy392-cmyk/VideoEditor.git
cd VideoEditor
```

> Sin git: en la web de GitHub cambia a esa rama, pulsa **Code → Download ZIP** y descomprímelo.
> Si más adelante fusionas la rama en `main`, quita `-b claude/serene-brown-fggcdl`.

---

## 3. Prepara un entorno de Python (solo la primera vez)

Así las librerías del editor no se mezclan con el resto de tu PC.

**Windows (PowerShell):**
```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements-extra.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-extra.txt
```

- `requirements-extra.txt` incluye **MediaPipe** (para recortar a la persona en la transición en la
  que cae sobre la pantalla). Si da problemas al instalarlo, usa `pip install -r requirements.txt`:
  todo funciona igual y esa transición pasa a ser un deslizamiento normal.
- Opcional, para que lea los **textos** de tus videos de referencia, instala Tesseract:
  Windows `winget install UB-Mannheim.TesseractOCR` · macOS `brew install tesseract tesseract-lang` ·
  Linux `sudo apt install tesseract-ocr tesseract-ocr-spa`.

---

## 4. Comprueba que todo está bien

```bash
python -m videoeditor doctor
```

Verás una lista con `[ OK ]` o lo que falte y cómo instalarlo en tu sistema. Si dice
*"todo lo imprescindible está listo"*, ya puedes usarlo.

---

## 5. Úsalo (cada día)

Cada vez que quieras editar, abre la terminal en la carpeta `VideoEditor` y:

**Windows:**
```powershell
.venv\Scripts\activate
python -m videoeditor serve
```
**macOS / Linux:**
```bash
source .venv/bin/activate
python -m videoeditor serve
```

Se abre el navegador en **http://127.0.0.1:8765** (si no se abre, escribe esa dirección).
Para cerrar el editor, vuelve a la terminal y pulsa **Ctrl + C**.

### El flujo de trabajo

1. **Biblioteca** → sube tus **clips**, tu **canción** y algún **video de referencia** que te guste.
   En la canción pulsa **Analizar** para ver sus secciones y el drop.
2. **Estilos** → elige el video de referencia, ponle nombre y pulsa **Aprender**
   (o usa el estilo incluido **Gym noche**). Más detalles en
   [ENSENAR_TECNICAS.md](ENSENAR_TECNICAS.md).
3. **Nuevo edit** → estilo, canción, clips (en orden), LUT de color e intensidad → **Crear edit**.
4. **Editor** → revisa la línea de tiempo, cambia lo que quieras (clic en un plano), **Vista previa
   rápida** para ver el resultado en pocos segundos y **Render final** para el video definitivo.
5. El video final aparece en **Biblioteca → Renders** y en la carpeta `workspace/renders/`.

### Dónde se guarda todo

Dentro de la carpeta `VideoEditor/workspace/`:

| Carpeta | Contenido |
|---|---|
| `clips/`, `music/`, `references/` | lo que subes |
| `styles/` | los estilos que aprende |
| `luts/` | tus LUTs `.cube` |
| `projects/` | los proyectos (puedes copiarlos para hacer variantes) |
| `renders/` | los videos terminados |

Para usar otra carpeta (por ejemplo en un disco externo):
`python -m videoeditor -w D:\MisEdits serve`.

---

## 6. Actualizar a una versión nueva

```bash
git pull
pip install -r requirements-extra.txt
```

Tus clips, estilos y proyectos (`workspace/`) no se tocan al actualizar.

---

## Problemas frecuentes

| Problema | Solución |
|---|---|
| `python` no se reconoce (Windows) | Usa `py` en vez de `python`, o reinstala Python marcando "Add Python to PATH". |
| `ffmpeg` no se encuentra | Cierra y abre la terminal tras instalarlo. Comprueba con `ffmpeg -version`. |
| `doctor` dice que falta `zscale`, `vidstab` o `libx264` | Tu FFmpeg es la versión reducida. Instala la *full* (en Windows, `Gyan.FFmpeg` o el `.7z` *full*). El editor funciona igual, pero sin convertir el HDR del iPhone, sin estabilizar o con peor calidad. |
| El puerto 8765 está ocupado | `python -m videoeditor serve --port 8800` |
| Los renders van lentos | Usa **Vista previa rápida** mientras editas y el render final solo al terminar. Los videos 4K del iPhone tardan más: no hace falta convertirlos antes. |
| Los clips del iPhone salen grises o lavados | Son HDR. `doctor` debe mostrar `zscale` disponible (FFmpeg *full*). |

---

## Sin interfaz (línea de comandos)

Todo lo de la web también funciona desde la terminal:

```bash
python -m videoeditor music cancion.mp3                       # analizar la canción
python -m videoeditor learn ref1.mp4 ref2.mp4 --name "Mi estilo"
python -m videoeditor auto --style "Mi estilo" --lut oro_gym -m cancion.mp3 c1.mov c2.mov c3.mov c4.mov -o edit.mp4
python -m videoeditor render edit.json --preview              # volver a renderizar tras editar el JSON
```

`python -m videoeditor --help` muestra todos los comandos.
