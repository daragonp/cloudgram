"""Script de mantenimiento: organiza los archivos ya subidos en la nube
por categoría (Documentos, Imágenes, etc.).

Ejecútalo manualmente desde el entorno del bot (por ejemplo
`python -m src.scripts.categorize_existing`).
"""
import asyncio
import os
from src.init_services import dropbox_svc, drive_svc
from src.handlers.message_handlers import get_file_category, FILE_CATEGORIES

async def categorize_dropbox():
    print("\n📁 Iniciando categorización en Dropbox...")
    # Listar contenido de la raíz
    entries = await dropbox_svc.list_files("")
    for name in entries:
        # ignorar carpetas (normalmente devuelve solo nombres)
        # el método list_files ya filtra por carpeta/archivo indistintamente,
        # pero asumimos que aquí sólo vienen los nombres.
        category = get_file_category(name) or "Otros"
        # si ya está en folder de categoría no hacer nada
        if name in FILE_CATEGORIES or category == "Otros":
            # si se llama igual a la carpeta, saltar
            continue
        dest_folder = category
        source = f"/{name}"
        dest = f"/{dest_folder}/{name}".replace("//", "/")
        print(f"  - Moviendo {source} -> {dest}")
        result = await dropbox_svc.move_file(source, dest)
        if result:
            print(f"    ✓ trasladado a {result}")
        else:
            print(f"    ⚠️ no se pudo mover {name}")

async def categorize_drive():
    print("\n📁 Iniciando categorización en Google Drive...")
    svc = drive_svc._get_service()
    # obtener todos los archivos en la raíz (no carpetas)
    page_token = None
    while True:
        resp = svc.files().list(
            q="'root' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, parents)",
            pageToken=page_token
        ).execute()
        for f in resp.get('files', []):
            # saltar carpetas (mimeType contiene "folder")
            if f.get('mimeType', '').endswith('folder'):
                continue
            name = f['name']
            category = get_file_category(name) or "Otros"
            if category == "Otros":
                continue
            # asegurar que existe carpeta y tenemos su id
            from main import CATEGORY_FOLDER_CACHE
            folder_id = CATEGORY_FOLDER_CACHE['drive'].get(category)
            if not folder_id:
                folder_id = await drive_svc.create_folder(category, parent_id=None)
                CATEGORY_FOLDER_CACHE['drive'][category] = folder_id
            # comprobar si ya está dentro de esa carpeta
            parents = f.get('parents', []) or []
            if folder_id in parents:
                continue
            print(f"  - Moviendo {name} ({f['id']}) a carpeta {category}")
            await drive_svc.move_file(f['id'], folder_id)
        page_token = resp.get('nextPageToken', None)
        if not page_token:
            break

async def main():
    # Asegurar que las carpetas de categoría existen y la cache esté poblada
    try:
        from main import ensure_category_folders
        await ensure_category_folders()
    except Exception as e:
        print(f"⚠️ No se pudo inicializar carpetas (cache): {e}")
    
    await categorize_dropbox()
    await categorize_drive()
    print("\n🏁 Categorización completada.")

if __name__ == '__main__':
    asyncio.run(main())
