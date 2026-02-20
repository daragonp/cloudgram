import fitz  # PyMuPDF para PDFs
import docx  # para archivos .docx
from PIL import Image
import pytesseract
from openai import OpenAI
import os
import numpy as np 
import pandas as pd
class AIHandler:
    @staticmethod
    def _get_client():
        # Se asegura de leer la API KEY justo cuando se va a usar
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    # src/utils/ai_handler.py

    @staticmethod
    async def get_embedding(text):
        """Convierte texto en un vector numérico manejando textos largos mediante promediado"""
        if not text: return None
        client = AIHandler._get_client()
        
        # Límite de seguridad para el modelo (aprox 8000 tokens)
        # 1 token ≈ 4 caracteres en promedio. 25,000 caracteres es un límite seguro.
        MAX_CHARS = 25000 
        
        try:
            if len(text) <= MAX_CHARS:
                response = client.embeddings.create(input=text, model="text-embedding-3-small")
                return response.data[0].embedding
            else:
                # Si es muy largo, dividimos en trozos
                print(f"✂️ Texto demasiado largo ({len(text)} chars). Fragmentando...")
                chunks = [text[i:i + MAX_CHARS] for i in range(0, len(text), MAX_CHARS)]
                
                all_embeddings = []
                for chunk in chunks:
                    res = client.embeddings.create(input=chunk, model="text-embedding-3-small")
                    all_embeddings.append(res.data[0].embedding)
                
                # Promediamos los vectores para obtener una representación global
                avg_embedding = np.mean(all_embeddings, axis=0).tolist()
                return avg_embedding
                
        except Exception as e:
            print(f"❌ Error en Embeddings: {e}")
            return None
        
    @staticmethod
    async def analyze_image(file_path):
        return "IA: Análisis de imagen (Funcionalidad en desarrollo)."
    
    @staticmethod
    async def get_embedding(text):
        """Convierte texto en un vector numérico para búsqueda IA"""
        if not text: return None
        client = AIHandler._get_client()
        # Usamos el modelo más eficiente y económico
        response = client.embeddings.create(input=text, model="text-embedding-3-small")
        return response.data[0].embedding
    
    @staticmethod
    async def transcribe_audio(file_path):
    
        """Usa Whisper de OpenAI para transcribir audio"""
        client = AIHandler._get_client()
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file
            )
        return transcript.text

    @staticmethod
    async def extract_text(file_path):
        ext = file_path.lower().split('.')[-1]
        text = ""

        try:
            if ext == 'pdf':
                doc = fitz.open(file_path)
                text = chr(12).join([page.get_text() for page in doc])
            elif ext == 'docx':
                doc = docx.Document(file_path)
                text = "\n".join([para.text for para in doc.paragraphs])
            elif ext in ['xlsx', 'xls']:
                # Leer todas las hojas del Excel
                df = pd.read_excel(file_path, sheet_name=None)
                text = "\n".join([sheet.to_string() for sheet in df.values()])
            elif ext in ['jpg', 'jpeg', 'png']:
                text = pytesseract.image_to_string(Image.open(file_path))
            elif ext == 'txt':
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            # Si es audio o video, usamos Whisper directamente
            elif ext in ['ogg', 'mp3', 'wav', 'mp4', 'm4a']:
                text = await AIHandler.transcribe_audio(file_path)
        except Exception as e:
            print(f"Error profundo extrayendo de {ext}: {e}")
        
        return text.strip()