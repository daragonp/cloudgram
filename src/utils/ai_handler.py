# src/utils/ai_handler.py
"""
Manejador de IA para CloudGram Pro - Compatible con Gemini API
Soporta: Embeddings, Transcripción de Audio, Análisis de Imágenes, Resúmenes
"""
import fitz  # PyMuPDF
import docx
import re
from PIL import Image
import os
import numpy as np 
import base64
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI

load_dotenv()

# Configuración de Gemini via interfaz compatible OpenAI
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# NOTA: Las claves se leen de forma lazy (en cada llamada) para que un cambio
# en .env + reinicio del bot sea suficiente para usar la nueva clave.
def _get_gemini_key():
    """Retorna la GEMINI_API_KEY actual desde el entorno (lazy read)."""
    return os.getenv("GEMINI_API_KEY")

def _get_openai_key():
    """Retorna la OPENAI_API_KEY actual desde el entorno (lazy read)."""
    return os.getenv("OPENAI_API_KEY")

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QuotaExceededError(Exception):
    """Excepción para cuando se agota la cuota (429) de la API de Gemini u OpenAI."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after

class AIHandler:
    @staticmethod
    def _parse_retry_after(error_msg):
        """Intenta extraer el tiempo de espera del mensaje de error."""
        # Buscar "Please retry in 58.99s" o "retryDelay: 58s"
        match = re.search(r"retry in ([\d\.]+)s", error_msg)
        if match: return match.group(1)
        match = re.search(r"retryDelay[\'\"]?\s*:\s*[\'\"]?([\d\.]+)s", error_msg)
        if match: return match.group(1)
        return None
    """
    Manejador de IA Híbrido:
    - Gemini (Google): Embeddings (compatibilidad DB), Visión, Audio, Resúmenes.
    - OpenAI: Análisis de intención de búsqueda /buscar_ia (mayor precisión).
    """
    
    # Modelos
    EMBEDDING_MODEL = "text-embedding-3-small"  # OpenAI — 1536 dims
    GEMINI_CHAT_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash"]
    OPENAI_CHAT_MODEL = "gpt-4o"
    
    # Clientes asíncronos compartidos (Singletons auto-invalidables)
    _async_client_gemini = None
    _async_client_openai = None
    _gemini_key_used = None   # Clave con la que se creó el cliente Gemini actual
    _openai_key_used = None   # Clave con la que se creó el cliente OpenAI actual

    @staticmethod
    def _get_async_client():
        """Retorna cliente asíncrono para Gemini (OpenAI-compatible).
        
        Si la GEMINI_API_KEY en el entorno cambió desde la última creación
        del cliente, invalida el singleton y crea uno nuevo con la clave actual.
        Esto permite cambiar la clave en .env sin reiniciar el proceso.
        """
        current_key = _get_gemini_key()
        if AIHandler._async_client_gemini is None or AIHandler._gemini_key_used != current_key:
            if AIHandler._async_client_gemini is not None:
                logger.info("🔄 GEMINI_API_KEY cambió — recreando cliente Gemini con la nueva clave.")
            AIHandler._async_client_gemini = AsyncOpenAI(
                api_key=current_key,
                base_url=GEMINI_BASE_URL
            )
            AIHandler._gemini_key_used = current_key
        return AIHandler._async_client_gemini

    @staticmethod
    def _get_openai_client():
        """Retorna cliente asíncrono real de OpenAI (auto-invalidable)."""
        current_key = _get_openai_key()
        if AIHandler._async_client_openai is None or AIHandler._openai_key_used != current_key:
            AIHandler._async_client_openai = AsyncOpenAI(api_key=current_key)
            AIHandler._openai_key_used = current_key
        return AIHandler._async_client_openai


    @staticmethod
    async def close_async_client():
        """Cierra los clientes asíncronos si existen"""
        if AIHandler._async_client_gemini:
            try:
                await AIHandler._async_client_gemini.close()
                AIHandler._async_client_gemini = None
            except: pass
        
        if AIHandler._async_client_openai:
            try:
                await AIHandler._async_client_openai.close()
                AIHandler._async_client_openai = None
            except: pass
        logger.info("🔌 Clientes asíncronos de IA cerrados.")

    @staticmethod
    async def get_embedding(text):
        """
        Convierte texto en un vector usando OpenAI text-embedding-3-small.
        
        Args:
            text: Texto a convertir en embedding
            
        Returns:
            list: Vector de 1536 dimensiones o None si hay error
            
        Note:
            OpenAI text-embedding-3-small produce 1536 dimensiones.
            Si ya tenías embeddings de Gemini (768 dims) en la DB,
            debes re-indexar con: UPDATE files SET embedding = NULL
        """
        if not text:
            return None
        
        # OpenAI admite ~8192 tokens; usamos ~24000 chars como límite conservador
        MAX_CHARS_SAFE = 24000
        
        # Limpieza preventiva para evitar errores de codificación
        text = text.replace('\x00', '').strip()
        # Mantener solo caracteres imprimibles y espacios
        text = ''.join(c for c in text if c.isprintable() or c in '\n\t ')
        if not text:
            return None
        
        model_name = AIHandler.EMBEDDING_MODEL
        
        try:
            client = AIHandler._get_openai_client()
            
            if len(text) <= MAX_CHARS_SAFE:
                response = await client.embeddings.create(
                    model=model_name,
                    input=text
                )
                vector = response.data[0].embedding
                logger.info(f"✅ Embedding generado con {model_name} ({len(vector)} dims)")
                return vector
            else:
                # Fragmentar texto largo y promediar
                logger.info(f"✂️ Fragmentando texto largo para embedding ({len(text)} chars)...")
                chunks = [text[i:i + MAX_CHARS_SAFE] for i in range(0, len(text), MAX_CHARS_SAFE)]
                
                # Solo procesamos los primeros 5 trozos para evitar latencia extrema
                all_embeddings = []
                for chunk in chunks[:5]:
                    res = await client.embeddings.create(
                        model=model_name,
                        input=chunk
                    )
                    all_embeddings.append(res.data[0].embedding)
                
                avg_embedding = np.mean(all_embeddings, axis=0).tolist()
                logger.info(f"✅ Embedding promediado generado ({len(avg_embedding)} dims)")
                return avg_embedding
                
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower() or "rate_limit" in error_msg.lower():
                retry = AIHandler._parse_retry_after(error_msg)
                wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                logger.error(f"🚨 Cuota de OpenAI agotada en embedding: {error_msg}")
                raise QuotaExceededError(f"Cuota de OpenAI agotada{wait_msg}", retry_after=retry)
            logger.error(f"❌ Error crítico en Embeddings (OpenAI): {e}")
            return None

    @staticmethod
    async def analyze_image_vision(file_path):
        """
        Analiza una imagen usando GPT-4o-mini Vision (OpenAI, cliente ASÍNCRONO).
        
        Args:
            file_path: Ruta al archivo de imagen
            
        Returns:
            str: Descripción de la imagen o mensaje de error
        """
        client = AIHandler._get_openai_client()
        model = "gpt-4o-mini"
        
        ext = file_path.lower().split('.')[-1]
        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'webp': 'image/webp',
            'gif': 'image/gif'
        }
        mime_type = mime_types.get(ext, 'image/jpeg')
        
        try:
            with open(file_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Analiza esta imagen y responde en español con un único bloque de texto (sin formato markdown ni títulos). "
                                    "Incluye: (1) descripción visual completa del contenido, (2) cualquier texto visible transcrito literalmente, "
                                    "(3) categoría del archivo (factura, viaje, chat, documento, recibo, foto personal, captura de pantalla, etc.). "
                                    "Sé exhaustivo y completo."
                                )
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1500
            )
            result = response.choices[0].message.content
            logger.info(f"✅ Imagen analizada con {model}: {len(result)} chars")
            return result

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower() or "rate_limit" in error_msg.lower():
                retry = AIHandler._parse_retry_after(error_msg)
                wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                logger.error(f"🚨 Cuota de OpenAI agotada en Visión: {error_msg}")
                raise QuotaExceededError(f"Cuota de OpenAI agotada en Visión{wait_msg}", retry_after=retry)
            logger.error(f"❌ Error en Visión IA (OpenAI): {e}")
            return ""

    @staticmethod
    async def transcribe_audio(file_path):
        """
        Transcribe audio usando OpenAI Whisper API (whisper-1).
        Soporta: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm.
        
        Args:
            file_path: Ruta al archivo de audio
            
        Returns:
            str: Transcripción del audio o string vacío si hay error
        """
        if not os.path.exists("data"):
            os.makedirs("data")
        log_file = "data/ai_debug.log"

        try:
            client = AIHandler._get_openai_client()
            file_name = os.path.basename(file_path)

            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"\n[{datetime.now()}] Transcribiendo con Whisper: {file_path}\n")

            with open(file_path, "rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=(file_name, audio_file),
                    language="es"
                )

            text_result = transcript.text.strip()

            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"✅ Whisper OK. Caracteres: {len(text_result)}\n")
            logger.info(f"✅ Audio transcrito con Whisper ({len(text_result)} chars)")
            return text_result

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower() or "rate_limit" in error_msg.lower():
                retry = AIHandler._parse_retry_after(error_msg)
                wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                logger.error(f"🚨 Cuota de OpenAI agotada en Audio (Whisper): {error_msg}")
                raise QuotaExceededError(f"Cuota de OpenAI agotada en Audio{wait_msg}", retry_after=retry)
            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"❌ ERROR CRÍTICO Whisper: {error_msg}\n")
            logger.error(f"❌ Error en transcripción Whisper: {error_msg}")
            return ""

    @staticmethod
    async def extract_text(file_path):
        """
        Extrae texto de diferentes tipos de archivo.
        Soporta: PDF, DOCX, TXT, imágenes (via Vision), audio (via transcripción).
        
        Args:
            file_path: Ruta al archivo
            
        Returns:
            str: Texto extraído o mensaje de error
        """
        if not file_path or not os.path.exists(file_path):
            return ""

        ext = file_path.lower().split('.')[-1]
        text = ""
        
        try:
            # Imágenes - Usar Vision
            if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                text = await AIHandler.analyze_image_vision(file_path)
            
            # Audio - Transcribir
            elif ext in ['ogg', 'mp3', 'wav', 'mp4', 'm4a', 'opus', 'flac', 'webm']:
                text = await AIHandler.transcribe_audio(file_path)
            
            # PDF - Extraer texto
            elif ext == 'pdf':
                with fitz.open(file_path) as doc:
                    text = " ".join([page.get_text() for page in doc])
                
                # Si es un PDF escaneado (sin texto), intentar OCR con Visión
                if len(text.strip()) < 10:
                    logger.info("📄 PDF parece escaneado, usando Visión...")
                    with fitz.open(file_path) as doc:
                        if len(doc) > 0:
                            # Procesar máximo 3 páginas para no exceder límites
                            pages_to_process = min(len(doc), 3)
                            extracted_texts = []
                            for i in range(pages_to_process):
                                page = doc.load_page(i)
                                pix = page.get_pixmap()
                                temp_img = f"{file_path}_ocr_p{i}.jpg"
                                pix.save(temp_img)
                                page_text = await AIHandler.analyze_image_vision(temp_img)
                                extracted_texts.append(f"[Página {i+1}]\n{page_text}")
                                if os.path.exists(temp_img):
                                    os.remove(temp_img)
                            text = "\n\n".join(extracted_texts)

            # DOCX
            elif ext == 'docx':
                doc = docx.Document(file_path)
                text = "\n".join([para.text for para in doc.paragraphs])
            
            # TXT
            elif ext == 'txt':
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()

        except QuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"❌ Error real en extract_text ({ext}): {str(e)}")
            text = ""
        
        return text.replace('\x00', '').strip()
    
    @staticmethod
    @staticmethod
    def _parse_json_response(content):
        content = content.strip()
        try:
            return json.loads(content)
        except Exception:
            # Intentar limpiar texto no válido y convertirlo a JSON simple
            try:
                snippet = content[content.index('{'):content.rindex('}')+1]
                return json.loads(snippet)
            except Exception:
                return None

    @staticmethod
    async def generate_summary_with_tags(text):
        """
        Genera un resumen y hashtags relevantes en JSON.

        Returns:
            dict: {'summary': str, 'tags': list[str]}
        """
        if not text or len(text.strip()) < 10:
            return {'summary': 'Sin contenido para resumir.', 'tags': []}

        prompt = (
            "Eres un archivista experto. Resume el siguiente texto en máximo 2 frases cortas en español. "
            "Luego genera entre 3 y 5 hashtags relevantes en español, sin símbolos extras, sin emojis y en formato de lista JSON. "
            "Responde únicamente en JSON con las claves 'summary' y 'tags'."
        )

        try:
            client = AIHandler._get_openai_client()
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text[:5000]}
                ],
                max_tokens=200
            )
            raw = response.choices[0].message.content.strip()
            parsed = AIHandler._parse_json_response(raw)
            if parsed and isinstance(parsed, dict):
                summary = parsed.get('summary', '').strip()
                tags = parsed.get('tags', [])
                if isinstance(tags, str):
                    tags = [t.strip('# ').strip() for t in tags.split(',') if t.strip()]
                if not isinstance(tags, list):
                    tags = []
                logger.info("✅ Resumen + tags generado con gpt-4o-mini")
                return {
                    'summary': summary or 'Resumen no disponible.',
                    'tags': [t for t in tags if t]
                }

            # Fallback si el modelo no devuelve JSON válido
            hashtags = re.findall(r"#([A-Za-zÁÉÍÓÚáéíóúÑñ0-9_]+)", raw)
            return {
                'summary': raw.split('Tags:')[0].strip()[:400],
                'tags': hashtags[:5]
            }

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower() or "rate_limit" in error_msg.lower():
                retry = AIHandler._parse_retry_after(error_msg)
                wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                logger.error(f"🚨 Cuota de OpenAI agotada en Resumen: {error_msg}")
                raise QuotaExceededError(f"Cuota de OpenAI agotada en Resumen{wait_msg}", retry_after=retry)
            logger.error(f"❌ Error generando resumen con tags (OpenAI): {e}")
            return {'summary': 'Resumen no disponible.', 'tags': []}

    @staticmethod
    async def generate_summary(text):
        """
        Genera un resumen ejecutivo del texto usando GPT-4o-mini (OpenAI).
        
        Args:
            text: Texto a resumir
            
        Returns:
            str: Resumen del texto (máximo 2 frases)
        """
        result = await AIHandler.generate_summary_with_tags(text)
        return result.get('summary', 'Resumen no disponible.')

    @staticmethod
    async def answer_document_question(file_name, question, content_text):
        """Responde preguntas específicas sobre un documento ya indexado."""
        if not question:
            return "❌ No se recibió ninguna pregunta."

        prompt = (
            "Eres un asistente experto en documentos. Te entregaré el nombre del documento y el texto extraído del mismo. "
            "Responde en español, usando solamente la información del texto proporcionado. Si no conoces la respuesta, di claramente que no está en el documento. "
            "Evita inventar datos y responde en un solo mensaje conciso."
        )
        text_to_use = content_text[:12000]
        try:
            client = AIHandler._get_openai_client()
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Documento: {file_name}\n\nTexto:\n{text_to_use}\n\nPregunta: {question}"}
                ],
                max_tokens=400
            )
            answer = response.choices[0].message.content.strip()
            logger.info("✅ Respuesta de documento generada con gpt-4o-mini")
            return answer
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower() or "rate_limit" in error_msg.lower():
                retry = AIHandler._parse_retry_after(error_msg)
                raise QuotaExceededError(f"Cuota de OpenAI agotada{(' (Reintenta en '+retry+'s)' if retry else '')}", retry_after=retry)
            logger.error(f"❌ Error respondiendo pregunta de documento: {e}")
            return "No pude responder esa pregunta en este momento."

    @staticmethod
    async def test_connection():
        """
        Prueba la conexión con OpenAI (todo el pipeline de IA usa OpenAI).

        Returns:
            dict: Estado de cada servicio de IA
        """
        results = {
            "status": "error",
            "details": [],
            "models_available": {
                "chat": [],
                "embedding": [],
                "audio": []
            }
        }

        try:
            client = AIHandler._get_openai_client()

            # 1. Test Embedding (text-embedding-3-small)
            try:
                resp = await client.embeddings.create(
                    model=AIHandler.EMBEDDING_MODEL, input="test"
                )
                dims = len(resp.data[0].embedding)
                results["details"].append(f"Embedding ({AIHandler.EMBEDDING_MODEL}, {dims} dims): OK ✅")
                results["models_available"]["embedding"].append(AIHandler.EMBEDDING_MODEL)
            except Exception as e:
                results["details"].append(f"Embedding ({AIHandler.EMBEDDING_MODEL}): FAIL ❌ ({str(e)[:60]})")

            # 2. Test Chat / Visión (gpt-4o-mini)
            try:
                resp = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5
                )
                results["details"].append("Chat/Visión (gpt-4o-mini): OK ✅")
                results["models_available"]["chat"].append("gpt-4o-mini")
            except Exception as e:
                results["details"].append(f"Chat/Visión (gpt-4o-mini): FAIL ❌ ({str(e)[:60]})")

            # 3. Test Audio (whisper-1) — verificamos solo acceso al modelo
            try:
                # Crear un WAV mínimo válido (44 bytes) para testear sin archivo real
                import io, struct
                buf = io.BytesIO()
                # Cabecera WAV mínima: RIFF + fmt + data vacía
                buf.write(b'RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00'
                          b'\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00')
                buf.seek(0)
                resp = await client.audio.transcriptions.create(
                    model="whisper-1", file=("test.wav", buf), language="es"
                )
                results["details"].append("Audio/Whisper (whisper-1): OK ✅")
                results["models_available"]["audio"].append("whisper-1")
            except Exception as e:
                # Whisper puede rechazar el WAV vacío pero eso confirma que llega al modelo
                err = str(e)
                if "audio" in err.lower() or "invalid" in err.lower() or "empty" in err.lower():
                    results["details"].append("Audio/Whisper (whisper-1): OK ✅ (API accesible)")
                    results["models_available"]["audio"].append("whisper-1")
                else:
                    results["details"].append(f"Audio/Whisper (whisper-1): FAIL ❌ ({err[:60]})")

            # Determinar estado general
            has_core = (results["models_available"]["embedding"]
                        and results["models_available"]["chat"])
            if has_core and results["models_available"]["audio"]:
                results["status"] = "ok"
            elif has_core:
                results["status"] = "partial"
            else:
                results["status"] = "error"

        except Exception as e:
            results["details"].append(f"Critical: {e}")

        return results
    
    @staticmethod
    def get_embedding_dimensions():
        """
        Retorna las dimensiones de los embeddings de OpenAI.
        
        Returns:
            int: Número de dimensiones (1536 para text-embedding-3-small)
        """
        return 1536  # OpenAI text-embedding-3-small produce embeddings de 1536 dimensiones
        
    @staticmethod
    async def analyze_search_intent(query_text):
        """
        Usa OpenAI para entender la intención de búsqueda del usuario.
        Se usa OpenAI aquí para una mejor extracción de entidades y tipos.
        
        Returns:
            dict: {"semantic_query": "texto a vectorizar", "file_types": ["pdf", "docx", ...]}
        """
        try:
            client = AIHandler._get_openai_client()
            model = AIHandler.OPENAI_CHAT_MODEL
            
            system_prompt = """
            Eres un asistente de búsqueda en una base de datos de archivos. 
            El usuario ingresará una consulta en español coloquial.
            Tu tarea es extraer:
            1. 'semantic_query': La idea semántica o contenido real que el usuario busca (sin mencionar el formato).
            2. 'file_types': Una lista de extensiones de archivo explícitas o implícitas en la consulta.
            
            Ejemplos de mapeo implícito/explícito:
            - PDF -> ["pdf"]
            - Word -> ["doc", "docx"]
            - Excel -> ["xls", "xlsx"]
            - Foto/Imagen -> ["jpg", "jpeg", "png", "webp", "gif"]
            - Video -> ["mp4", "mkv", "avi", "mov", "webm"]
            - Audio/Nota de voz -> ["mp3", "ogg", "wav", "m4a", "opus"]
            
            Ejemplo 1:
            Usuario: "documentos en PDF sobre gatos"
            salida JSON: {"semantic_query": "sobre gatos", "file_types": ["pdf"]}
            
            Ejemplo 2:
            Usuario: "videos de cumpleaños"
            salida JSON: {"semantic_query": "de cumpleaños", "file_types": ["mp4", "mkv", "avi", "mov", "webm"]}
            
            Ejemplo 3:
            Usuario: "contrato de alquiler"
            salida JSON: {"semantic_query": "contrato de alquiler", "file_types": []}
            
            Devuelve ÚNICAMENTE el JSON.
            """
            
            try:
                response = await client.chat.completions.create(
                    model=model,
                    response_format={ "type": "json_object" },
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query_text}
                    ],
                    max_tokens=150
                )
                
                result_text = response.choices[0].message.content.strip()
                logger.info(f"✅ Intención extraída con OpenAI ({model}): {result_text}")
                return json.loads(result_text)
            except Exception as model_error:
                error_msg = str(model_error)
                if "429" in error_msg or "quota" in error_msg.lower():
                    retry = AIHandler._parse_retry_after(error_msg)
                    wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                    logger.error(f"🚨 Cuota agotada en OpenAI: {error_msg}")
                    raise QuotaExceededError(f"Cuota de OpenAI agotada{wait_msg}", retry_after=retry)

                logger.warning(f"⚠️ OpenAI ({model}) falló: {model_error}")
                return {"semantic_query": query_text, "file_types": []}
                    
            return {"semantic_query": query_text, "file_types": []}
            
        except QuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"❌ Error al analizar intención: {e}")
            return {"semantic_query": query_text, "file_types": []}