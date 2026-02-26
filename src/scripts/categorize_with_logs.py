"""Script de categorización mejorado con streaming de logs.
Diseñado para ser llamado desde web_admin.py con SSE.
"""
import asyncio
import dropbox
from src.init_services import dropbox_svc, drive_svc, db
from src.handlers.message_handlers import get_file_category, FILE_CATEGORIES


async def categorize_with_logs():
    """Async generator que yield logs de categorización en tiempo real."""
    
    # Cargar caché desde BD al inicio
    category_cache = db.load_category_cache()
    
    yield "[SISTEMA] ✨ Iniciando categorización de archivos..."
    yield "[SISTEMA] Evaluando cache de carpetas..."
    
    # Dropbox
    yield "[DROPBOX] 🔍 Explorando estructura de Dropbox..."
    dbx = dropbox_svc.dbx
    if not dbx:
        yield "[DROPBOX] ❌ Dropbox no disponible"
    else:
        files_moved = 0
        try:
            def list_dropbox_recursive(path='', paths_stack=None):
                """Genera lista de rutas a procesar sin usar async yield"""
                if paths_stack is None:
                    paths_stack = [path]
                
                all_entries = []
                errors = []
                while paths_stack:
                    current_path = paths_stack.pop(0)
                    try:
                        res = dbx.files_list_folder(current_path)
                        for entry in res.entries:
                            if isinstance(entry, dropbox.files.FolderMetadata):
                                if entry.name not in list(FILE_CATEGORIES.keys()) + ["Otros"]:
                                    paths_stack.append(entry.path_lower)
                            else:
                                all_entries.append(entry)
                    except Exception as e:
                        errors.append(f"[DROPBOX] ⚠️  Error listando {current_path}: {e}")
                
                return all_entries, errors
            
            entries, errors = list_dropbox_recursive('')
            
            for error in errors:
                yield error

            for entry in entries:
                name = entry.name
                category = get_file_category(name) or "Otros"
                yield f"[DROPBOX] 📄 {entry.path_lower} → {category}"
                
                if category == "Otros":
                    yield f"[DROPBOX]    (Otros: no se mueve)"
                    continue
                
                target_folder = f"/{category}"
                current_folder = entry.path_lower.rsplit('/', 1)[0] if '/' in entry.path_lower else ''
                
                if current_folder.strip('/').lower() == category.lower():
                    yield f"[DROPBOX]    ✓ Ya en la carpeta correcta"
                    continue
                
                source = entry.path_lower
                dest = f"{target_folder}/{name}".replace("//", "/")
                yield f"[DROPBOX]    📤 Moviendo a {dest}"
                
                moved = await dropbox_svc.move_file(source, dest)
                if moved:
                    yield f"[DROPBOX]    ✅ Movido exitosamente"
                    files_moved += 1
                else:
                    yield f"[DROPBOX]    ❌ No se pudo mover"
            
            yield f"[DROPBOX] ✓ Completado. {files_moved} archivos movidos en Dropbox."
        except Exception as e:
            yield f"[DROPBOX] ❌ Error: {str(e)}"
    
    # Google Drive
    yield "[DRIVE] 🔍 Explorando estructura de Google Drive..."
    try:
        svc = drive_svc._get_service()
        files_moved_drive = 0
        
        # Poblar cache de carpetas de categoría
        for cat in list(FILE_CATEGORIES.keys()) + ["Otros"]:
            if cat not in category_cache['drive']:
                cat_id = await drive_svc.create_folder(cat, parent_id=None)
                category_cache['drive'][cat] = cat_id
                db.save_category_folder(cat, 'drive', cat_id)
                yield f"[DRIVE] 📁 Carpeta de categoría '{cat}' creada/verificada"

        
        def list_drive_recursive(folder_id='root', path_name="root"):
            """Lista archivos recursivamente de Google Drive"""
            all_files = []
            paths_stack = [(folder_id, path_name)]
            
            while paths_stack:
                current_id, current_path = paths_stack.pop(0)
                page_token = None
                
                while True:
                    resp = svc.files().list(
                        q=f"'{current_id}' in parents and trashed=false",
                        spaces="drive",
                        fields="nextPageToken, files(id, name, mimeType, parents)",
                        pageToken=page_token
                    ).execute()
                    
                    for f in resp.get('files', []):
                        if f.get('mimeType', '').endswith('folder'):
                            if f['name'] not in list(FILE_CATEGORIES.keys()) + ["Otros"]:
                                paths_stack.append((f['id'], current_path + "/" + f['name']))
                        else:
                            all_files.append((f, current_path))
                    
                    page_token = resp.get('nextPageToken', None)
                    if not page_token:
                        break
            
            return all_files
        
        files = list_drive_recursive()
        for f, path_name in files:
            name = f['name']
            category = get_file_category(name) or "Otros"
            yield f"[DRIVE] 📄 {path_name}/{name} → {category}"
            
            if category == "Otros":
                yield f"[DRIVE]    (Otros: no se mueve)"
                continue
            
            folder_cat_id = category_cache['drive'].get(category)
            if not folder_cat_id:
                yield f"[DRIVE]    ⚠️  Carpeta {category} no en cache, creando..."
                folder_cat_id = await drive_svc.create_folder(category, parent_id=None)
                category_cache['drive'][category] = folder_cat_id
                db.save_category_folder(category, 'drive', folder_cat_id)
            
            parents = f.get('parents', []) or []
            if folder_cat_id in parents:
                yield f"[DRIVE]    ✓ Ya en la carpeta correcta"
                continue
            
            yield f"[DRIVE]    📤 Moviendo a carpeta {category}"
            result = await drive_svc.move_file(f['id'], folder_cat_id)
            if result:
                yield f"[DRIVE]    ✅ Movido exitosamente"
                files_moved_drive += 1
            else:
                yield f"[DRIVE]    ❌ No se pudo mover"
        
        yield f"[DRIVE] ✓ Completado. {files_moved_drive} archivos movidos en Google Drive."
    except Exception as e:
        yield f"[DRIVE] ❌ Error: {str(e)}"
    
    yield "[SISTEMA] ✅ Categorización finalizada con éxito."
