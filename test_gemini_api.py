import asyncio
import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

async def test_gemini():
    api_key = os.getenv("GEMINI_API_KEY")
    print(f"Using API Key: {api_key[:10]}...")
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    try:
        print("Testing text generation...")
        response = model.generate_content("Hola, dime 'OK' si recibes esto.")
        print(f"Response: {response.text}")
        
        print("Testing embedding...")
        result = genai.embed_content(
            model="models/text-embedding-004",
            content="Test text"
        )
        print(f"Embedding success: {len(result['embedding'])} dimensions")
        
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_gemini())
