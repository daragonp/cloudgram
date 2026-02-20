☁️ CloudGram Pro v1.0
CloudGram es un bot de Telegram avanzado diseñado para la gestión inteligente de archivos. Permite recibir, renombrar, transcribir y subir archivos automáticamente a nubes como Dropbox y Google Drive, integrando búsqueda semántica mediante Inteligencia Artificial.

🚀 Funcionalidades Principales
📦 Gestión de Archivos y Multimedia
Detección Automática: Soporta Documentos, Fotos, Videos, Notas de Video, Audios y Notas de Voz.

Cola de Subida: Permite enviar múltiples archivos y elegir el destino de forma masiva.

Nombres Únicos: Sistema antifallo que combina timestamps y sufijos aleatorios para evitar que los archivos se sobrescriban.

Ubicaciones: Convierte coordenadas GPS de Telegram en archivos de texto con direcciones legibles (vía Geopy).

🤖 Inteligencia Artificial (OpenAI)
Transcripción: Convierte notas de voz y audios a texto usando el modelo Whisper-1.

Indexación Automática: Extrae texto de PDFs, Word (.docx), Excel (.xlsx) y fotos (OCR con Tesseract).

Búsqueda Semántica: No solo busca por nombre, sino por "concepto" usando Embeddings (text-embedding-3-small). Si buscas "gastos", encontrará el Excel de "Control de actividades".

Fragmentación (Chunking): Capacidad para procesar archivos de texto gigantes (como Excels de 40k+ tokens) dividiéndolos y promediando sus vectores.

☁️ Integración con Nubes
Dropbox: Conexión permanente mediante Refresh Tokens (OAuth2).

Google Drive: Subida automática a carpetas configuradas.

Enlaces Directos: Los links generados permiten la visualización directa (dl=1 en Dropbox).

🛠️ Requisitos Técnicos
Dependencias de Software (Sistema)
Python 3.10+

Tesseract OCR: Necesario para leer texto en imágenes.

macOS: brew install tesseract tesseract-lang

Linux: sudo apt install tesseract-ocr

SQLite3: Motor de base de datos (incluido en Python).

Librerías de Python
Bash
pip install python-telegram-bot openai dropbox google-api-python-client 
pip install pandas openpyxl pytesseract pymupdf python-docx numpy geopy python-dotenv
⚙️ Configuración del Entorno (.env)
Crea un archivo .env en la raíz del proyecto con las siguientes claves:

Fragmento de código
# Telegram
TELEGRAM_BOT_TOKEN=tu_token_de_botfather

# OpenAI
OPENAI_API_KEY=tu_clave_de_openai

# Dropbox (OAuth2 con Refresh Token)
DROPBOX_APP_KEY=tu_app_key
DROPBOX_APP_SECRET=tu_app_secret
DROPBOX_REFRESH_TOKEN=tu_refresh_token_permanente

# Google Drive (opcional según implementación)
GOOGLE_DRIVE_CREDENTIALS_JSON=credentials.json
📂 Estructura del Proyecto
Plaintext
cloudgram/
├── main.py                # Punto de entrada y manejo de callbacks
├── indexador.py           # Script para procesar archivos pendientes
├── src/
│   ├── database/          # db_handler.py (SQLite)
│   ├── handlers/          # message_handlers.py (Lógica de Telegram)
│   ├── services/          # dropbox_service.py, drive_service.py
│   └── utils/             # ai_handler.py (OpenAI, OCR, Chunking)
├── descargas/             # Carpeta temporal de procesamiento
└── data/                  # Almacenamiento de base de datos
📖 Guía de Uso para el Usuario
Enviar archivos: Envía uno o varios archivos al bot.

Seleccionar Nube: Pulsa los botones de Dropbox o Google Drive (aparecerá un ✅).

IA (Opcional): Si es un audio, pulsa "Transcribir con IA" para subir el texto de lo que se dijo.

Confirmar: Pulsa 🚀 CONFIRMAR SUBIDA.

Comandos Disponibles:
/listar: Muestra los últimos 20 archivos subidos con sus links.

/buscar [palabra]: Búsqueda exacta por nombre de archivo.

/buscar_ia [concepto]: Búsqueda inteligente por contenido o contexto.

/eliminar: Borra registros de la base de datos.

⚠️ Consideraciones Importantes
Tokens de Dropbox: No uses el "Access Token" generado manualmente en la consola, ya que caduca en 4 horas. Debes generar el refresh_token siguiendo el flujo OAuth2.

Límites de OpenAI: El sistema de Chunking está configurado para evitar el error de "Context Length" promediando vectores de archivos grandes.

Privacidad: Los archivos se descargan temporalmente en la carpeta /descargas y se eliminan inmediatamente después de subir a la nube o procesar la IA.

SSL en macOS: El proyecto incluye una corrección para el error de certificados de certifi común en sistemas macOS al usar geopy.

==============================================================
🛠️ Instalación y Configuración
Sigue estos pasos para poner en marcha tu propia instancia de CloudGram Pro:

1. Clonar el repositorio
Bash
git clone https://github.com/tu-usuario/cloudgram.git
cd cloudgram
2. Configurar el Entorno Virtual
Es recomendable usar un entorno virtual para mantener las dependencias aisladas:

Bash
python3 -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
3. Configuración de Variables de Entorno
Copia el archivo de ejemplo y rellena tus credenciales:

Bash
cp .env.example .env
Nota: Nunca subas el archivo .env al repositorio. El archivo .gitignore ya está configurado para protegerlo.

4. Credenciales de Google Drive
Para usar Google Drive, debes obtener un archivo credentials.json desde la Consola de Google Cloud:

Crea un proyecto nuevo.

Habilita la Google Drive API.

Crea una Cuenta de Servicio y descarga la llave en formato JSON.

Guarda el archivo como credentials.json en la raíz del proyecto.

5. Generar Refresh Token de Dropbox
Como los Access Tokens de Dropbox caducan cada 4 horas, debes generar un Refresh Token de larga duración:

Crea una App en el Dropbox App Console.

Usa el flujo de autorización offline para obtener tu código inicial.

Intercambia ese código por un refresh_token usando el endpoint de token de Dropbox.

Añade el token resultante a tu archivo .env.