import fitz  # PyMuPDF para PDFs
import docx  # para archivos .docx
from PIL import Image
import os
import numpy as np 
import pandas as pd
import base64
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class AIHandler:
    
    @staticmethod
    def _get_client():
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    @staticmethod
    async def get_embedding(text):
        """Convierte texto en un vector. Maneja fragmentación real para evitar errores de context length."""
        if not text: return None
        client = AIHandler._get_client()
        
        # Bajamos a 8000 caracteres para asegurar que nunca exceda los 8192 tokens del modelo
        MAX_CHARS_SAFE = 8000 
        
        try:
            # Limpieza preventiva para evitar errores de codificación en el envío
            text = text.replace('\x00', '')
            
            if len(text) <= MAX_CHARS_SAFE:
                response = client.embeddings.create(input=text, model="text-embedding-3-small")
                return response.data[0].embedding
            else:
                print(f"✂️ Fragmentando texto largo para embedding ({len(text)} chars)...")
                # Dividimos en trozos seguros
                chunks = [text[i:i + MAX_CHARS_SAFE] for i in range(0, len(text), MAX_CHARS_SAFE)]
                
                # Solo procesamos los primeros 5 trozos para evitar latencia extrema y costos
                all_embeddings = []
                for chunk in chunks[:5]: 
                    res = client.embeddings.create(input=chunk, model="text-embedding-3-small")
                    all_embeddings.append(res.data[0].embedding)
                
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
                model="gpt-4o-mini",
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
        client = AIHandler._get_client()
        try:
            with open(file_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file
                )
            return transcript.text
        except Exception as e:
            print(f"❌ Error en Whisper: {e}")
            return ""

    @staticmethod
    async def extract_text(file_path):
        """Extrae texto y lo limpia de caracteres prohibidos para la DB."""
        ext = file_path.lower().split('.')[-1]
        text = ""

        try:
            if ext in ['jpg', 'jpeg', 'png', 'webp']:
                text = await AIHandler.analyze_image_vision(file_path)
            elif ext == 'pdf':
                doc = fitz.open(file_path)
                text = " ".join([page.get_text() for page in doc])
            elif ext == 'docx':
                doc = docx.Document(file_path)
                text = "\n".join([para.text for para in doc.paragraphs])
            elif ext in ['xlsx', 'xls']:
                df = pd.read_excel(file_path, sheet_name=None)
                text = "\n".join([sheet.to_string() for sheet in df.values()])
            elif ext == 'txt':
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            elif ext in ['ogg', 'mp3', 'wav', 'mp4', 'm4a']:
                text = await AIHandler.transcribe_audio(file_path)
                
        except Exception as e:
            print(f"❌ Error extrayendo de {ext}: {e}")
        
        # LIMPIEZA FINAL OBLIGATORIA: Evita que el bot se rompa al insertar en Supabase
        return text.replace('\x00', '').strip()