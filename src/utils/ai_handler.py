import fitz
import docx
from PIL import Image
import os
import numpy as np 
import base64
from openai import OpenAI, AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Configuración de Gemini via interfaz compatible OpenAI
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

class AIHandler:
    
    @staticmethod
    def _get_client():
        return OpenAI(
            api_key=GEMINI_API_KEY,
            base_url=GEMINI_BASE_URL
        )

    @staticmethod
    async def get_embedding(text):
        """Convierte texto en un vector. Maneja fragmentación real para evitar errores de context length."""
        if not text: return None
        
        # Bajamos a 8000 caracteres para asegurar que nunca exceda los tokens del modelo
        MAX_CHARS_SAFE = 8000 
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            
            # Limpieza preventiva para evitar errores de codificación en el envío
            text = text.replace('\x00', '')
            
            if len(text) <= MAX_CHARS_SAFE:
                result = genai.embed_content(
                    model="models/text-embedding-004",
                    content=text
                )
                return result['embedding']
            else:
                print(f"✂️ Fragmentando texto largo para embedding ({len(text)} chars)...")
                # Dividimos en trozos seguros
                chunks = [text[i:i + MAX_CHARS_SAFE] for i in range(0, len(text), MAX_CHARS_SAFE)]
                
                # Solo procesamos los primeros 5 trozos para evitar latencia extrema
                all_embeddings = []
                for chunk in chunks[:5]: 
                    res = genai.embed_content(
                        model="models/text-embedding-004",
                        content=chunk
                    )
                    all_embeddings.append(res['embedding'])
                
                avg_embedding = np.mean(all_embeddings, axis=0).tolist()
                return avg_embedding
                
        except Exception as e:
            print(f"❌ Error en Embeddings: {e}")
            return None

    @staticmethod
    async def analyze_image_vision(file_path):
        client = AIHandler._get_client()
        try:
            with open(file_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            response = client.chat.completions.create(
                model="gemini-2.0-flash",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe esta imagen detalladamente. Si hay texto, transcríbelo. Clasifícala (factura, viaje, chat, etc)."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ],
                    }
                ],
                max_tokens=400
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"❌ Error en Visión IA: {e}")
            return "Error en análisis de visión."

    @staticmethod
    async def transcribe_audio(file_path):
        """Transcribe audio usando la API nativa de Google Gemini (gratis)."""
        try:
            import google.generativeai as genai
            from google.generativeai.types import HarmCategory, HarmBlockThreshold
            
            genai.configure(api_key=GEMINI_API_KEY)
            
            # Configuramos el modelo con filtros de seguridad desactivados para evitar falsos positivos
            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash",
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                }
            )
            
            mime_types = {
                'ogg': 'audio/ogg', # Para notas de voz de Telegram
                'mp3': 'audio/mp3',
                'wav': 'audio/wav', 
                'mp4': 'audio/mp4', 
                'm4a': 'audio/mp4'
            }
            ext = file_path.lower().split('.')[-1]
            # Si es OGG, usamos un MIME type más compatible si es necesario, 
            # aunque 'audio/ogg' suele ser suficiente para Gemini.
            mime_type = mime_types.get(ext, 'audio/ogg')
            
            print(f"🎙️ Transcribiendo ({mime_type}): {os.path.basename(file_path)}...")
            
            with open(file_path, "rb") as f:
                audio_data = f.read()
            
            # Usamos un prompt más robusto y pedimos que no sea tan restrictivo
            response = model.generate_content([
                {
                    "mime_type": mime_type, 
                    "data": audio_data
                },
                "Actúa como un transcriptor profesional. Transcribe el contenido de este audio de manera literal y completa. "
                "Si no hay voz clara, describe brevemente el sonido o indica que no hay contenido hablado. "
                "Solo devuelve el texto transcrito sin preámbulos ni comentarios."
            ])
            
            if not response.text:
                print("⚠️ Gemini devolvió una respuesta vacía.")
                return "No se pudo extraer texto del audio (posible silencio o ruido)."
                
            return response.text.strip()
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Error en transcripción de audio: {error_msg}")
            # Si el error es de seguridad de Gemini a pesar de los settings
            if "finish_reason: SAFETY" in error_msg:
                return "[Error: El contenido fue bloqueado por filtros de seguridad de la IA]"
            return f"[Error en transcripción: {error_msg}]"

    @staticmethod
    async def extract_text(file_path):
        if not file_path or not os.path.exists(file_path):
            return ""

        ext = file_path.lower().split('.')[-1]
        text = ""
        try:
            if ext in ['jpg', 'jpeg', 'png', 'webp']:
                text = await AIHandler.analyze_image_vision(file_path)
            
            elif ext in ['ogg', 'mp3', 'wav', 'mp4', 'm4a']:
                text = await AIHandler.transcribe_audio(file_path)
            
            elif ext == 'pdf':
                with fitz.open(file_path) as doc:
                    text = " ".join([page.get_text() for page in doc])
                
                # Si es un PDF escaneado (sin texto), intentar OCR con Visión
                if len(text.strip()) < 10:
                    print("pdf parece escaneado, usando Visión...")
                    with fitz.open(file_path) as doc:
                        if len(doc) > 0:
                            page = doc.load_page(0)
                            pix = page.get_pixmap()
                            temp_img = f"{file_path}_ocr.jpg"
                            pix.save(temp_img)
                            text = await AIHandler.analyze_image_vision(temp_img)
                            if os.path.exists(temp_img): os.remove(temp_img)

            elif ext == 'docx':
                doc = docx.Document(file_path)
                text = " ".join([para.text for para in doc.paragraphs])
            
            elif ext == 'txt':
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()

        except Exception as e:
            print(f"❌ Error real en extract_text ({ext}): {str(e)}")
            text = f"Error al extraer texto de {ext}"
        
        return text.replace('\x00', '').strip()
    
    @staticmethod
    async def generate_summary(text):
        """Genera un resumen ejecutivo del texto para mostrar en búsquedas."""
        try:
            client = AsyncOpenAI(
                api_key=GEMINI_API_KEY,
                base_url=GEMINI_BASE_URL
            )
            
            response = await client.chat.completions.create(
                model="gemini-2.0-flash",
                messages=[
                    {"role": "system", "content": "Eres un archivista experto. Resume el siguiente texto en máximo 2 frases cortas que describan de qué trata el documento."},
                    {"role": "user", "content": text[:4000]}
                ],
                max_tokens=100
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generando resumen: {e}")
            return "Resumen no disponible."