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
    EMBEDDING_MODELS = ["gemini-embedding-001", "gemini-embedding-2-preview"]
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
        Convierte texto en un vector usando la API nativa de Google Gemini.
        
        Args:
            text: Texto a convertir en embedding
            
        Returns:
            list: Vector de 768 dimensiones o None si hay error
            
        Note:
            Los embeddings de Gemini tienen 768 dimensiones.
            Los embeddings anteriores de OpenAI tenían 1536 dimensiones.
            NO son compatibles entre sí.
        """
        if not text:
            return None
        
        # Límite seguro para evitar errores de context length
        MAX_CHARS_SAFE = 8000 
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=_get_gemini_key())
            
            # Limpieza preventiva para evitar errores de codificación
            text = text.replace('\x00', '').strip()
            if not text:
                return None
            
            # Intentar con el modelo más reciente disponible
            for model_name in AIHandler.EMBEDDING_MODELS:
                try:
                    if len(text) <= MAX_CHARS_SAFE:
                        result = genai.embed_content(
                            model=f"models/{model_name}",
                            content=text
                        )
                        logger.info(f"✅ Embedding generado con {model_name} ({len(result['embedding'])} dims)")
                        return result['embedding']
                    else:
                        # Fragmentar texto largo
                        logger.info(f"✂️ Fragmentando texto largo para embedding ({len(text)} chars)...")
                        chunks = [text[i:i + MAX_CHARS_SAFE] for i in range(0, len(text), MAX_CHARS_SAFE)]
                        
                        # Solo procesamos los primeros 5 trozos para evitar latencia extrema
                        all_embeddings = []
                        for chunk in chunks[:5]:
                            res = genai.embed_content(
                                model=f"models/{model_name}",
                                content=chunk
                            )
                            all_embeddings.append(res['embedding'])
                        
                        # Promediar embeddings
                        avg_embedding = np.mean(all_embeddings, axis=0).tolist()
                        logger.info(f"✅ Embedding promediado generado ({len(avg_embedding)} dims)")
                        return avg_embedding
                        
                except Exception as model_error:
                    error_msg = str(model_error)
                    if "429" in error_msg or "ResourceExhausted" in error_msg or "quota" in error_msg.lower():
                        retry = AIHandler._parse_retry_after(error_msg)
                        wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                        logger.error(f"🚨 Cuota agotada en {model_name}: {error_msg}")
                        raise QuotaExceededError(f"Cuota de IA agotada{wait_msg}", retry_after=retry)
                    
                    logger.warning(f"⚠️ Modelo {model_name} falló: {model_error}")
                    continue
            
            logger.error("❌ Todos los modelos de embedding fallaron")
            return None
                
        except QuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"❌ Error crítico en Embeddings: {e}")
            return None

    @staticmethod
    async def analyze_image_vision(file_path):
        """
        Analiza una imagen usando Gemini Vision (cliente ASÍNCRONO).
        
        Args:
            file_path: Ruta al archivo de imagen
            
        Returns:
            str: Descripción de la imagen o mensaje de error
        """
        # IMPORTANTE: Usar cliente ASÍNCRONO para no bloquear el event loop
        client = AIHandler._get_async_client()
        
        # Detectar tipo de imagen
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

            # Intentar con diferentes modelos de chat (await porque es async)
            for model in AIHandler.GEMINI_CHAT_MODELS:
                try:
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
                                ],
                            }
                        ],
                        max_tokens=1500
                    )
                    result = response.choices[0].message.content
                    logger.info(f"✅ Imagen analizada con {model}: {len(result)} chars")
                    return result
                except Exception as model_error:
                    error_msg = str(model_error)
                    if "429" in error_msg or "ResourceExhausted" in error_msg or "quota" in error_msg.lower():
                        retry = AIHandler._parse_retry_after(error_msg)
                        wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                        logger.error(f"🚨 Cuota agotada en {model} (Visión): {error_msg}")
                        raise QuotaExceededError(f"Cuota de IA agotada en Visión{wait_msg}", retry_after=retry)

                    logger.warning(f"⚠️ Modelo {model} falló para visión: {model_error}")
                    continue
            
            logger.warning("No se pudo analizar la imagen con ningún modelo disponible.")
            return ""
            
        except QuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"❌ Error en Visión IA: {e}")
            return ""

    @staticmethod
    async def transcribe_audio(file_path):
        """
        Transcribe audio usando la API nativa de Google Gemini (gratis).
        Soporta múltiples formatos de audio.
        
        Args:
            file_path: Ruta al archivo de audio
            
        Returns:
            str: Transcripción del audio o mensaje de error
        """
        log_file = "data/ai_debug.log"
        if not os.path.exists("data"):
            os.makedirs("data")
        
        try:
            import google.generativeai as genai
            from google.generativeai.types import HarmCategory, HarmBlockThreshold
            
            genai.configure(api_key=_get_gemini_key())
            
            # Configuración de seguridad permisiva para transcripciones
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
            
            # Detectar MIME type
            ext = file_path.lower().split('.')[-1]
            mime_map = {
                'ogg': 'audio/ogg',
                'oga': 'audio/ogg',
                'mp3': 'audio/mpeg',
                'wav': 'audio/wav',
                'm4a': 'audio/mp4',
                'mp4': 'audio/mp4',
                'webm': 'audio/webm',
                'flac': 'audio/flac',
                'opus': 'audio/opus'
            }
            mime_type = mime_map.get(ext, f'audio/{ext}')
            
            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"\n[{datetime.now()}] Transcribiendo: {file_path} (MIME: {mime_type})\n")
            
            with open(file_path, "rb") as f:
                audio_data = f.read()
            
            # Intentar con diferentes modelos (Gemini nativo para audio)
            for model_name in AIHandler.GEMINI_CHAT_MODELS:
                try:
                    model = genai.GenerativeModel(
                        model_name=model_name,
                        safety_settings=safety_settings
                    )
                    
                    response = model.generate_content([
                        {
                            "mime_type": mime_type, 
                            "data": audio_data
                        },
                        "Actúa como un transcriptor profesional. Transcribe el contenido de este audio de manera literal y completa. "
                        "Si encuentras silencio o música, descríbelo entre corchetes. Solo devuelve la transcripción en español."
                    ])
                    
                    # Verificación exhaustiva de la respuesta
                    if not response.candidates:
                        with open(log_file, "a") as log:
                            log.write(f"⚠️ Sin candidatos en la respuesta con {model_name}.\n")
                        continue
                    
                    # Intentar extraer texto de forma segura
                    try:
                        text_result = response.text.strip()
                    except Exception as tex_err:
                        with open(log_file, "a") as log:
                            log.write(f"⚠️ Error accediendo a .text con {model_name}: {tex_err}\n")
                        if response.candidates[0].finish_reason:
                            continue
                        continue

                    if not text_result:
                        continue
                        
                    with open(log_file, "a", encoding="utf-8") as log:
                        log.write(f"✅ Éxito con {model_name}. Caracteres: {len(text_result)}\n")
                    
                    logger.info(f"✅ Audio transcrito con {model_name}")
                    return text_result
                    
                except Exception as model_error:
                    error_msg = str(model_error)
                    if "429" in error_msg or "ResourceExhausted" in error_msg or "quota" in error_msg.lower():
                        retry = AIHandler._parse_retry_after(error_msg)
                        wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                        logger.error(f"🚨 Cuota agotada en {model_name} (Audio): {error_msg}")
                        raise QuotaExceededError(f"Cuota de IA agotada en Audio{wait_msg}", retry_after=retry)

                    with open(log_file, "a") as log:
                        log.write(f"⚠️ Modelo {model_name} falló: {model_error}\n")
                    continue
            
            error_msg = "No se pudo transcribir el audio con ningún modelo disponible."
            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"❌ Todos los modelos fallaron\n")
            return ""

        except QuotaExceededError:
            raise
        except Exception as e:
            error_msg = str(e)
            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"❌ ERROR CRÍTICO: {error_msg}\n")
            logger.error(f"❌ Error en transcripción: {error_msg}")
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
    async def generate_summary(text):
        """
        Genera un resumen ejecutivo del texto para mostrar en búsquedas.
        
        Args:
            text: Texto a resumir
            
        Returns:
            str: Resumen del texto (máximo 2 frases)
        """
        if not text or len(text.strip()) < 10:
            return "Sin contenido para resumir."
            
        try:
            client = AIHandler._get_async_client()
            
            # Intentar con diferentes modelos de chat
            for model in AIHandler.GEMINI_CHAT_MODELS:
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system", 
                                "content": "Eres un archivista experto. Resume el siguiente texto en máximo 2 frases cortas que describan de qué trata el documento. Responde en español."
                            },
                            {
                                "role": "user", 
                                "content": text[:4000]  # Limitar entrada
                            }
                        ],
                        max_tokens=150
                    )
                    result = response.choices[0].message.content.strip()
                    logger.info(f"✅ Resumen generado con {model}")
                    return result
                except Exception as model_error:
                    error_msg = str(model_error)
                    if "429" in error_msg or "ResourceExhausted" in error_msg or "quota" in error_msg.lower():
                        retry = AIHandler._parse_retry_after(error_msg)
                        wait_msg = f" (Reintenta en {retry}s)" if retry else ""
                        logger.error(f"🚨 Cuota agotada en {model} (Resumen): {error_msg}")
                        raise QuotaExceededError(f"Cuota de IA agotada en Resumen{wait_msg}", retry_after=retry)

                    logger.warning(f"⚠️ Modelo {model} falló para resumen: {model_error}")
                    continue
            
            return "Resumen no disponible (error en todos los modelos)."
            
        except QuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"❌ Error generando resumen: {e}")
            return "Resumen no disponible."

    @staticmethod
    async def test_connection():
        """
        Prueba la conexión con Gemini y devuelve el estado de todos los servicios.
        
        Returns:
            dict: Estado de cada servicio de IA
        """
        results = {
            "status": "error", 
            "details": [],
            "models_available": {
                "chat": [],
                "embedding": []
            }
        }
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=_get_gemini_key())
            
            # 1. Test Chat Models (Gemini)
            for model in AIHandler.GEMINI_CHAT_MODELS:
                try:
                    test_model = genai.GenerativeModel(model)
                    resp = test_model.generate_content("ping")
                    if resp.text:
                        results["details"].append(f"Chat ({model}): OK ✅")
                        results["models_available"]["chat"].append(model)
                except Exception as e:
                    results["details"].append(f"Chat ({model}): FAIL ❌ ({str(e)[:50]})")

            # 2. Test Embedding Models
            for model in AIHandler.EMBEDDING_MODELS:
                try:
                    genai.embed_content(model=f"models/{model}", content="test")
                    results["details"].append(f"Embedding ({model}): OK ✅")
                    results["models_available"]["embedding"].append(model)
                except Exception as e:
                    results["details"].append(f"Embedding ({model}): FAIL ❌ ({str(e)[:50]})")

            # Determinar estado general
            if results["models_available"]["chat"] or results["models_available"]["embedding"]:
                results["status"] = "ok" if (results["models_available"]["chat"] and results["models_available"]["embedding"]) else "partial"
            else:
                results["status"] = "error"
                
        except Exception as e:
            results["details"].append(f"Critical: {e}")
        
        return results
    
    @staticmethod
    def get_embedding_dimensions():
        """
        Retorna las dimensiones de los embeddings de Gemini.
        
        Returns:
            int: Número de dimensiones (768 para Gemini)
        """
        return 768  # text-embedding-004 produce embeddings de 768 dimensiones
        
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