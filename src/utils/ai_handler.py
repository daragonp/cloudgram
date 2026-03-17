# src/utils/ai_handler.py
"""
Manejador de IA para CloudGram Pro - Compatible con Gemini API
Soporta: Embeddings, Transcripción de Audio, Análisis de Imágenes, Resúmenes
"""
import fitz  # PyMuPDF
import docx
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AIHandler:
    """
    Manejador de IA con soporte para Gemini API.
    Incluye fallback automático entre modelos y manejo robusto de errores.
    """
    
    # Modelos disponibles en orden de preferencia
    EMBEDDING_MODELS = ["gemini-embedding-001", "gemini-embedding-2-preview"]
    CHAT_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
    
    @staticmethod
    def _get_client():
        """Retorna cliente síncrono para chat/vision"""
        return OpenAI(
            api_key=GEMINI_API_KEY,
            base_url=GEMINI_BASE_URL
        )

    @staticmethod
    def _get_async_client():
        """Retorna cliente asíncrono para operaciones no bloqueantes"""
        return AsyncOpenAI(
            api_key=GEMINI_API_KEY,
            base_url=GEMINI_BASE_URL
        )

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
            genai.configure(api_key=GEMINI_API_KEY)
            
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
                    logger.warning(f"⚠️ Modelo {model_name} falló: {model_error}")
                    continue
            
            logger.error("❌ Todos los modelos de embedding fallaron")
            return None
                
        except Exception as e:
            logger.error(f"❌ Error crítico en Embeddings: {e}")
            return None

    @staticmethod
    async def analyze_image_vision(file_path):
        """
        Analiza una imagen usando Gemini Vision.
        
        Args:
            file_path: Ruta al archivo de imagen
            
        Returns:
            str: Descripción de la imagen o mensaje de error
        """
        client = AIHandler._get_client()
        
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

            # Intentar con diferentes modelos de chat
            for model in AIHandler.CHAT_MODELS:
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text", 
                                        "text": "Describe esta imagen detalladamente. Si hay texto, transcríbelo completo. Clasifícala en una categoría (factura, viaje, chat, documento, recibo, foto personal, etc). Responde en español."
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
                        max_tokens=500
                    )
                    result = response.choices[0].message.content
                    logger.info(f"✅ Imagen analizada con {model}")
                    return result
                except Exception as model_error:
                    logger.warning(f"⚠️ Modelo {model} falló para visión: {model_error}")
                    continue
            
            return "Error: No se pudo analizar la imagen con ningún modelo disponible."
            
        except Exception as e:
            logger.error(f"❌ Error en Visión IA: {e}")
            return f"Error en análisis de visión: {str(e)}"

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
            
            genai.configure(api_key=GEMINI_API_KEY)
            
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
            
            # Intentar con diferentes modelos
            for model_name in AIHandler.CHAT_MODELS:
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
                    with open(log_file, "a") as log:
                        log.write(f"⚠️ Modelo {model_name} falló: {model_error}\n")
                    continue
            
            error_msg = "No se pudo transcribir el audio con ningún modelo disponible."
            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"❌ Todos los modelos fallaron\n")
            return f"[{error_msg}]"

        except Exception as e:
            error_msg = str(e)
            with open(log_file, "a", encoding="utf-8") as log:
                log.write(f"❌ ERROR CRÍTICO: {error_msg}\n")
            logger.error(f"❌ Error en transcripción: {error_msg}")
            return f"[Error en transcripción: {error_msg}]"

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

        except Exception as e:
            logger.error(f"❌ Error real en extract_text ({ext}): {str(e)}")
            text = f"Error al extraer texto de {ext}: {str(e)}"
        
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
            
            # Intentar con diferentes modelos
            for model in AIHandler.CHAT_MODELS:
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
                    logger.warning(f"⚠️ Modelo {model} falló para resumen: {model_error}")
                    continue
            
            return "Resumen no disponible (error en todos los modelos)."
            
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
            genai.configure(api_key=GEMINI_API_KEY)
            
            # 1. Test Chat Models
            for model in AIHandler.CHAT_MODELS:
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