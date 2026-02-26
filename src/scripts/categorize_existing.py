"""Script de mantenimiento: organiza los archivos ya subidos en la nube
por categoría (Documentos, Imágenes, etc.).

Ejecútalo manualmente desde el entorno del bot (por ejemplo
`python -m src.scripts.categorize_existing`).
"""
import asyncio
import os
import dropbox
from src.init_services import dropbox_svc, drive_svc
from src.handlers.message_handlers import get_file_category, FILE_CATEGORIES

async def categorize_dropbox():
    print("\n📁 Iniciando categorización en Dropbox...")
    dbx = dropbox_svc.dbx

    async def process_folder(path):
        try:
            res = dbx.files_list_folder(path)
        except Exception as e:
            print(f"⚠️ Error listando {path}: {e}")
            return
        for entry in res.entries:
            if isinstance(entry, dropbox.files.FolderMetadata):
                # evitar descender en las carpetas de categoría y en "Otros"
                if entry.name in list(FILE_CATEGORIES.keys()) + ["Otros"]:
                    print(f"  - saltando carpeta de categoría {entry.path_lower}")
                    continue
                await process_folder(entry.path_lower)
            elif isinstance(entry, dropbox.files.FileMetadata):
                name = entry.name
                category = get_file_category(name) or "Otros"
                parent = path.lstrip('/') or 'root'
                print(f"  archivo {entry.path_lower} (carpeta padre={parent}) -> categoría {category}")
                if category == "Otros":
                    print("    categoría Otros, no se mueve")
                    continue
                target_folder = f"/{category}"
                # si ya está en la carpeta correcta, saltar
                if parent.lower() == category.lower():
                    print("    ya en la carpeta correspondiente, saltando")
                    continue
                source = entry.path_lower
                dest = f"{target_folder}/{name}".replace("//", "/")
                print(f"    moviendo {source} -> {dest}")
                moved = await dropbox_svc.move_file(source, dest)
                if moved:
                    print(f"    ✓ movido a {moved}")
                else:
                    print(f"    ⚠️ no se pudo mover {name}")
            else:
                print(f"  - tipo desconocido en {entry.path_lower}, omitiendo")

    await process_folder('')

async def categorize_drive():
    print("\n📁 Iniciando categorización en Google Drive...")
    svc = drive_svc._get_service()

    async def process_folder(folder_id, path_name="root"):
        # lista el contenido de la carpeta indicada
        page_token = None
        while True:
            resp = svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, parents)",
                pageToken=page_token
            ).execute()
            for f in resp.get('files', []):
                if f.get('mimeType', '').endswith('folder'):
                    # evitar carpetas de categoría
                    if f['name'] in list(FILE_CATEGORIES.keys()) + ["Otros"]:
                        print(f"  - saltando carpeta de categoría {f['name']} ({f['id']})")
                        continue
                    await process_folder(f['id'], path_name + "/" + f['name'])
                else:
                    name = f['name']
                    category = get_file_category(name) or "Otros"
                    print(f"  archivo Drive {path_name}/{name} -> categoría {category}")
                    if category == "Otros":
                        print("    categoría Otros, no se mueve")
                        continue
                    from main import CATEGORY_FOLDER_CACHE
                    folder_cat_id = CATEGORY_FOLDER_CACHE['drive'].get(category)
                    if not folder_cat_id:
                        folder_cat_id = await drive_svc.create_folder(category, parent_id=None)
                        CATEGORY_FOLDER_CACHE['drive'][category] = folder_cat_id
                    parents = f.get('parents', []) or []
                    if folder_cat_id in parents:
                        print("    ya en carpeta, saltando")
                        continue
                    print(f"    moviendo {name} ({f['id']}) a carpeta {category}")
                    await drive_svc.move_file(f['id'], folder_cat_id)
            page_token = resp.get('nextPageToken', None)
            if not page_token:
                break
    # iniciar en la raíz
    await process_folder('root')


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
