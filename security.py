import mysql.connector
import bcrypt

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "clement_package_log",
}

def hash_password(password):
    """Hash a password using bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt)

def initialize_system():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    
    try:
        print("Initializing security system...")
        
        # Create a default hall
        cur.execute("SELECT id FROM halls WHERE hall_code = 'CLMT'")
        if not cur.fetchone():
            print("Creating Clement Hall...")
            cur.execute("INSERT INTO halls (hall_name, hall_code) VALUES ('Clement Hall', 'CLMT')")
            hall_id = cur.lastrowid
            
            # Create admin user
            admin_password = hash_password('admin123')
            cur.execute("""
                INSERT INTO initialscheck 
                (hall_id, username, initials, fullName, title, password_hash,
                 can_checkin, can_checkout, can_view_other_halls,
                 can_manage_users, can_manage_halls)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, TRUE, TRUE, TRUE, TRUE)
            """, (hall_id, 'admin', 'ADM', 'Hall Administrator', 'HD', 
                 admin_password.decode('utf-8')))
            
            print("Default hall and admin user created!")
            print("Admin credentials: admin / admin123")
        
        conn.commit()
        print("Initialization complete!")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    initialize_system()