import mysql.connector
import bcrypt
import secrets

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

def generate_username_from_initials(initials, existing_usernames):
    """Generate a unique username from initials"""
    base = initials.lower().replace(' ', '')
    username = base
    
    counter = 1
    while username in existing_usernames:
        username = f"{base}{counter}"
        counter += 1
    
    return username

def migrate_existing_users():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    
    try:
        print("Checking database structure...")
        
        # Check if halls table has data
        cur.execute("SELECT id, hall_code, hall_name FROM halls")
        halls = cur.fetchall()
        
        if not halls:
            print("No halls found! Creating default hall...")
            cur.execute("INSERT INTO halls (hall_name, hall_code) VALUES ('Clement Hall', 'CLMT')")
            conn.commit()
            cur.execute("SELECT id FROM halls WHERE hall_code = 'CLMT'")
            halls = cur.fetchall()
        
        hall_id = halls[0]['id'] if halls else 1
        print(f"Using hall ID: {hall_id}")
        
        # Get existing usernames to avoid duplicates
        cur.execute("SELECT username FROM initialscheck WHERE username IS NOT NULL")
        existing_usernames = {row['username'] for row in cur.fetchall()}
        
        # Get all existing users without hall_id
        cur.execute("""
            SELECT id, initials, fullName, title 
            FROM initialscheck 
            WHERE hall_id IS NULL OR hall_id = ''
        """)
        users = cur.fetchall()
        
        print(f"Found {len(users)} users to migrate...")
        
        migrated_count = 0
        for user in users:
            try:
                # Skip if no initials
                if not user.get('initials'):
                    print(f"Skipping user without initials: {user}")
                    continue
                
                # Create username from initials
                initials = user['initials'].strip()
                username = generate_username_from_initials(initials, existing_usernames)
                
                # Set default password
                temp_password = "temp123"
                password_hash = hash_password(temp_password)
                
                # Get user ID
                user_id = user['id']
                
                print(f"Migrating: {initials} -> username: {username}")
                
                # Update user with new security fields
                cur.execute("""
                    UPDATE initialscheck 
                    SET hall_id = %s, 
                        username = %s,
                        password_hash = %s,
                        temporary_password = TRUE,
                        is_active = TRUE,
                        created_at = COALESCE(created_at, NOW())
                    WHERE id = %s
                """, (hall_id, username, password_hash.decode('utf-8'), user_id))
                
                existing_usernames.add(username)
                migrated_count += 1
                
            except Exception as e:
                print(f"Error migrating user {user.get('initials', 'Unknown')}: {e}")
                continue
        
        conn.commit()
        print(f"\nMigration complete! {migrated_count} users migrated.")
        print("\nTemporary passwords set to 'temp123'")
        print("Users must change password on first login.")
        
        # Show migrated users
        print("\nMigrated users:")
        cur.execute("""
            SELECT username, initials, fullName, title 
            FROM initialscheck 
            WHERE hall_id = %s AND temporary_password = TRUE
            ORDER BY initials
        """, (hall_id,))
        
        migrated_users = cur.fetchall()
        for user in migrated_users:
            print(f"  {user['username']} ({user['initials']}) - {user['fullName']} - {user['title']}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def check_database_structure():
    """Check and fix database structure"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    
    try:
        print("Checking database tables and columns...")
        
        # Check if columns exist
        columns_to_check = [
            'hall_id', 'username', 'password_hash', 'temporary_password',
            'is_active', 'can_checkin', 'can_checkout', 'can_view_other_halls',
            'can_manage_users', 'can_manage_halls', 'created_by', 'created_at',
            'last_password_change'
        ]
        
        for column in columns_to_check:
            try:
                cur.execute(f"SHOW COLUMNS FROM initialscheck LIKE '{column}'")
                if not cur.fetchone():
                    print(f"Warning: Column '{column}' does not exist in initialscheck table")
            except:
                print(f"Could not check column '{column}'")
        
        # Check halls table
        cur.execute("SHOW TABLES LIKE 'halls'")
        if not cur.fetchone():
            print("ERROR: 'halls' table does not exist!")
            print("Please run the SQL commands first:")
            print("""
            CREATE TABLE halls (
                id INT AUTO_INCREMENT PRIMARY KEY,
                hall_name VARCHAR(100) NOT NULL UNIQUE,
                hall_code VARCHAR(10) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INT,
                is_active BOOLEAN DEFAULT TRUE
            );
            """)
            return False
        
        return True
        
    except Exception as e:
        print(f"Error checking database: {e}")
        return False
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("C3 User Migration Tool")
    print("=" * 60)
    
    # Check database first
    if not check_database_structure():
        print("\nDatabase structure issues detected!")
        print("Please run the SQL commands first before migrating users.")
        input("\nPress Enter to exit...")
        exit(1)
    
    print("\nStarting migration...")
    migrate_existing_users()
    
    print("\n" + "=" * 60)
    print("Migration process completed!")
    print("=" * 60)
    input("\nPress Enter to exit...")