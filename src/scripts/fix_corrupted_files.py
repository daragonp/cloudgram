import sys
import os

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from src.database.db_handler import DatabaseHandler

def fix_corrupted_files():
    print("🧹 Iniciando limpieza de archivos corruptos en la base de datos...")
    try:
        db = DatabaseHandler()
        affected = db.clean_corrupted_files()
        print(f"✅ Éxito! Se han blanqueado (limpiado) {affected} archivos corruptos.")
        print("💡 Estos archivos serán re-procesados automáticamente en tu próxima ejecución de indexación IA.")
        return affected
    except Exception as e:
        print(f"❌ Error durante la limpieza: {e}")
        return 0

if __name__ == "__main__":
    count = fix_corrupted_files()
