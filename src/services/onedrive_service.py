# src/services/onedrive_service.py
import os
import msal
import requests
from .base_service import CloudService

class OneDriveService(CloudService):
    def __init__(self, client_id, tenant_id):
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scopes = ["Files.ReadWrite.All"]
        self.token_file = "onedrive_token.json"
        self.access_token = None

    def _get_access_token(self):
        app = msal.PublicClientApplication(self.client_id, authority=self.authority)
        accounts = app.get_accounts()
        
        if accounts:
            result = app.acquire_token_silent(self.scopes, account=accounts[0])
            if result: return result['access_token']

        # Si no hay token guardado, usar flujo de código de dispositivo
        flow = app.initiate_device_flow(scopes=self.scopes)
        print(f"⚠️ {flow['message']}") # Esto saldrá en tu consola de VSCode/Terminal
        result = app.acquire_token_by_device_flow(flow)
        
        if "access_token" in result:
            return result['access_token']
        return None

    async def upload(self, local_path, file_name):
        token = self._get_access_token()
        if not token: return "Error de autenticación en OneDrive"

        endpoint = f"https://graph.microsoft.com/v1.0/me/drive/root:/{file_name}:/content"
        
        with open(local_path, "rb") as f:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
            response = requests.put(endpoint, headers=headers, data=f)

        if response.status_code in [200, 201]:
            # Obtener link compartido
            item_id = response.json().get('id')
            link_req = requests.post(
                f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/createLink",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"type": "view", "scope": "anonymous"}
            )
            return link_req.json().get('link', {}).get('webUrl', "Subido (sin link)")
        else:
            raise Exception(f"Error OneDrive: {response.text}")

    async def list_files(self, path="/"):
        # Implementación básica para cumplir el contrato
        return []