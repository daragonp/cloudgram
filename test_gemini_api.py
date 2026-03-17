#!/usr/bin/env python3
"""
Diagnóstico de API de Gemini - Ver qué modelos están disponibles
"""

import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print(f"\n🔑 API Key: {GEMINI_API_KEY[:15]}...")
print("="*60)

# 1. Listar todos los modelos
print("\n📋 LISTANDO MODELOS DISPONIBLES...")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"

try:
    req = urllib.request.Request(url, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        
        print(f"\n✅ {len(data.get('models', []))} modelos encontrados\n")
        
        for model in data.get("models", []):
            name = model.get("name", "")
            methods = model.get("supportedGenerationMethods", [])
            
            # Mostrar todos los modelos
            print(f"   {name}")
            if "embedContent" in methods:
                print(f"      ⭐ SOPORTA EMBEDDINGS: {methods}")
            elif methods:
                print(f"      Métodos: {methods}")
                
except urllib.error.HTTPError as e:
    error = e.read().decode()
    print(f"\n❌ Error {e.code}:")
    print(error)
except Exception as e:
    print(f"\n❌ Error: {e}")

# 2. Probar chat (para verificar que la API key funciona)
print("\n" + "="*60)
print("🧪 PROBANDO CHAT (verificar API key)...")

chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
chat_data = json.dumps({
    "contents": [{"parts": [{"text": "Di 'hola'"}]}]
}).encode('utf-8')

try:
    req = urllib.request.Request(chat_url, data=chat_data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode())
        if 'candidates' in result:
            print("   ✅ Chat funciona correctamente")
        else:
            print(f"   ⚠️ Respuesta inesperada: {result}")
except urllib.error.HTTPError as e:
    error = e.read().decode()
    print(f"   ❌ Error {e.code}")
    print(f"   {error[:200]}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 3. Probar embedding con diferentes formatos de API
print("\n" + "="*60)
print("🧪 PROBANDO EMBEDDINGS (diferentes formatos)...")

embedding_formats = [
    # v1 API
    ("v1 - text-embedding-004", f"https://generativelanguage.googleapis.com/v1/models/text-embedding-004:embedContent?key={GEMINI_API_KEY}"),
    ("v1 - embedding-001", f"https://generativelanguage.googleapis.com/v1/models/embedding-001:embedContent?key={GEMINI_API_KEY}"),
    # v1beta API
    ("v1beta - text-embedding-004", f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_API_KEY}"),
]

emb_data = json.dumps({
    "content": {"parts": [{"text": "test"}]}
}).encode('utf-8')

for name, url in embedding_formats:
    try:
        req = urllib.request.Request(url, data=emb_data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            if 'embedding' in result:
                dims = len(result['embedding'].get('values', []))
                print(f"   ✅ {name}: {dims} dimensiones")
            else:
                print(f"   ⚠️ {name}: Sin embedding - {result}")
    except urllib.error.HTTPError as e:
        print(f"   ❌ {name}: Error {e.code}")
    except Exception as e:
        print(f"   ❌ {name}: {str(e)[:50]}")

print("\n" + "="*60)