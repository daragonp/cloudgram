import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print(f"\n🔑 API Key: {GEMINI_API_KEY[:15]}...")

# Listar modelos
print("\n📋 LISTANDO MODELOS...")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"

try:
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Content-Type': 'application/json'})) as response:
        data = json.loads(response.read().decode())

        for model in data.get("models", []):
            name = model.get("name", "")
            methods = model.get("supportedGenerationMethods", [])
            if "embed" in name.lower() or "embedContent" in methods:
                print(f"   ⭐ {name} - {methods}")

except Exception as e:
    print(f"❌ Error: {e}")

# Probar chat
print("\n<0001f9ea> PROBANDO CHAT...")
chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
try:
    data = json.dumps({"contents": [{"parts": [{"text": "di hola"}]}]}).encode()
    with urllib.request.urlopen(urllib.request.Request(chat_url, data=data, headers={'Content-Type': 'application/json'})) as r:
        print("   ✅ Chat OK")
except Exception as e:
    print(f"   ❌ {e}")