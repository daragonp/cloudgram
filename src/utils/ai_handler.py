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
        # Se asegura de leer la API KEY justo cuando se va a usar
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    @staticmethod
    async def get_embedding(text):
        """Convierte texto en un vector numérico manejando textos largos mediante promediado"""
        if not text: return None
        client = AIHandler._get_client()
        
        # 25,000 caracteres es un límite seguro para el modelo text-embedding-3-small
        MAX_CHARS = 25000 
        
        try:
            if len(text) <= MAX_CHARS:
                response = client.embeddings.create(input=text, model="text-embedding-3-small")
                return response.data[0].embedding
            else:
                # Si es muy largo (como el Excel de 300k chars), dividimos en trozos
                print(f"✂️ Texto demasiado largo ({len(text)} chars). Fragmentando para Embedding...")
                chunks = [text[i:i + MAX_CHARS] for i in range(0, len(text), MAX_CHARS)]
                
                all_embeddings = []
                for chunk in chunks:
                    res = client.embeddings.create(input=chunk, model="text-embedding-3-small")
                    all_embeddings.append(res.data[0].embedding)
                
                # Promediamos los vectores para obtener una representación semántica global
                avg_embedding = np.mean(all_embeddings, axis=0).tolist()
                return avg_embedding
                
        except Exception as e:
            print(f"❌ Error en Embeddings: {e}")
            return None

    @staticmethod
    async def analyze_image_vision(file_path):
        """Usa GPT-4o-mini Vision para describir imágenes y capturas de pantalla"""
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
                            {"type": "text", "text": "Describe esta imagen detalladamente. Si hay texto, transcríbelo. Explica qué es para poder encontrarla después buscando por contexto (ej: factura, foto de viaje, captura de chat, etc)."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ],
                    }
                ],
                max_tokens=400
            )
            description = response.choices[0].message.content
            print(f"👁️ Cámara IA: {description[:60]}...")
            return description
        except Exception as e:
            print(f"❌ Error en Visión IA: {e}")
            return "Error al analizar imagen con visión artificial."

    @staticmethod
    async def transcribe_audio(file_path):
        """Usa Whisper de OpenAI para transcribir audio"""
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
        """Punto de entrada principal para extraer significado de cualquier archivo"""
        ext = file_path.lower().split('.')[-1]
        text = ""

        try:
            # --- IMÁGENES (Cámara IA con Visión) ---
            if ext in ['jpg', 'jpeg', 'png', 'webp']:
                text = await AIHandler.analyze_image_vision(file_path)

            # --- DOCUMENTOS DE TEXTO ---
            elif ext == 'pdf':
                doc = fitz.open(file_path)
                text = chr(12).join([page.get_text() for page in doc])
            elif ext == 'docx':
                doc = docx.Document(file_path)
                text = "\n".join([para.text for para in doc.paragraphs])
            elif ext in ['xlsx', 'xls']:
                # Leer todas las hojas del Excel
                df = pd.read_excel(file_path, sheet_name=None)
                text = "\n".join([sheet.to_string() for sheet in df.values()])
            elif ext == 'txt':
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()

            # --- AUDIO Y VIDEO (Whisper) ---
            elif ext in ['ogg', 'mp3', 'wav', 'mp4', 'm4a']:
                text = await AIHandler.transcribe_audio(file_path)
                
        except Exception as e:
            print(f"❌ Error profundo extrayendo de {ext}: {e}")
        
        return text.strip()