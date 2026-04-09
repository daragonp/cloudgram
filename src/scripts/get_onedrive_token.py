import msal
import os
import sys
from dotenv import load_dotenv

# Añadir el directorio raíz al path para importar src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

load_dotenv()

def get_token():
    client_id = os.getenv("ONEDRIVE_CLIENT_ID")
    client_secret = os.getenv("ONEDRIVE_CLIENT_SECRET")
    
    if not client_id or not client_secret or client_id == "tu_client_id_de_azure":
        print("❌ ERROR: Primero debes configurar ONEDRIVE_CLIENT_ID y ONEDRIVE_CLIENT_SECRET en tu archivo .env")
        return

    authority = "https://login.microsoftonline.com/common"
    scopes = ["Files.ReadWrite.All"]

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret
    )

    # 1. Obtener la URL de autorización
    auth_url = app.get_authorization_request_url(scopes, redirect_uri="http://localhost:5000/callback")
    
    print("\n" + "="*60)
    print("🔑 OBTENCIÓN DE REFRESH TOKEN PARA ONEDRIVE")
    print("="*60)
    print(f"\n1. Abre esta URL en tu navegador e inicia sesión:\n\n{auth_url}\n")
    print("2. Después de autorizar, serás redirigido a una página que no cargará (localhost).")
    print("3. COPIA LA URL COMPLETA de la barra de direcciones del navegador.")
    print("="*60 + "\n")

    full_url = input("Pega aquí la URL completa a la que fuiste redirigido: ").strip()
    
    try:
        # Extraer el código de la URL
        import urllib.parse as urlparse
        parsed = urlparse.urlparse(full_url)
        code = urlparse.parse_qs(parsed.query).get('code')
        
        if not code:
            print("❌ No se encontró el código en la URL. Asegúrate de copiarla toda.")
            return

        result = app.acquire_token_by_authorization_code(
            code[0],
            scopes=scopes,
            redirect_uri="http://localhost:5000/callback"
        )

        if "refresh_token" in result:
            print("\n" + "✅"*10)
            print("¡ÉXITO!")
            print(f"\nTu ONEDRIVE_REFRESH_TOKEN es:\n\n{result['refresh_token']}\n")
            print("Cópialo y pégalo en tu archivo .env")
            print("✅"*10 + "\n")
        else:
            print(f"❌ Error al obtener el token: {result.get('error_description', result.get('error'))}")
            
    except Exception as e:
        print(f"❌ Error procesando la URL: {e}")

if __name__ == "__main__":
    get_token()
