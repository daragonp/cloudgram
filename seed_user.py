from werkzeug.security import generate_password_hash
from src.database.db_handler import DatabaseHandler
import os

def seed():
    # 1. Inicializamos tu manejador (esto ejecutará _setup_initial_db)
    db_manager = DatabaseHandler("cloudgram.db")
    
    email = "admin@local.com"
    password = "admin123"
    name = "Administrador"
    
    # Generamos el hash con el método correcto
    pass_hash = generate_password_hash(password, method='scrypt')
    
    # 2. Insertamos usando una conexión directa para asegurar que no haya fallos
    with db_manager._connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, pass_hash)
            )
            conn.commit()
            print(f"✅ Usuario creado con éxito!")
            print(f"📧 Email: {email}")
            print(f"🔑 Password: {password}")
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                print("⚠️ El usuario ya existe en la base de datos.")
            else:
                print(f"❌ Error al crear usuario: {e}")

if __name__ == "__main__":
    seed()