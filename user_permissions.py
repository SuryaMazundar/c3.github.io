# admin_password_manager.py - ENHANCED VERSION
import mysql.connector
import bcrypt
import getpass
import secrets
import string

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "clement_package_log",
}

def hash_password(password):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt)

def generate_temp_password(length=12):
    """Generate a temporary password"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def list_existing_halls(cur):
    """List all existing halls"""
    cur.execute("SELECT id, hall_name, hall_code FROM halls ORDER BY hall_name")
    halls = cur.fetchall()
    
    if not halls:
        print("No halls found in the database!")
        return None
    
    print("\nExisting Halls:")
    print("-" * 40)
    for i, hall in enumerate(halls, 1):
        print(f"{i}. {hall['hall_name']} ({hall['hall_code']})")
    print("-" * 40)
    
    return halls

def list_all_users_with_details(cur, hall_id):
    """List all users for a hall with detailed information"""
    cur.execute("""
        SELECT id, username, initials, fullName, title, is_active, 
               temporary_password, can_checkin, can_checkout,
               can_view_other_halls, can_manage_users, can_manage_halls
        FROM initialscheck 
        WHERE hall_id = %s
        ORDER BY title DESC, username
    """, (hall_id,))
    users = cur.fetchall()
    
    if users:
        print(f"\nExisting users for this hall:")
        print("-" * 100)
        for user in users:
            status = "ACTIVE" if user['is_active'] else "INACTIVE"
            password_status = "TEMP" if user['temporary_password'] else "PERM"
            role = user['title']
            
            if role == 'HD':
                role_desc = "Hall Director"
            elif role == 'AHD':
                role_desc = "Assistant Hall Director"
            elif role == 'OA':
                role_desc = "Office Assistant"
            else:
                role_desc = role
            
            # Permission indicators
            permissions = []
            if user['can_checkin']: permissions.append("CI")
            if user['can_checkout']: permissions.append("CO")
            if user['can_view_other_halls']: permissions.append("VH")
            if user['can_manage_users']: permissions.append("MU")
            if user['can_manage_halls']: permissions.append("MH")
            
            perm_str = "[" + ",".join(permissions) + "]" if permissions else "[No Perms]"
            
            print(f"  • {user['username']} ({user['initials']}) - {role_desc} - {status} - {password_status} {perm_str}")
        print("-" * 100)
    
    return users

def get_password_input(prompt="Password (min 8 chars): ", require_min_length=True):
    """Get password with validation"""
    while True:
        password = getpass.getpass(prompt)
        
        if require_min_length and len(password) < 8:
            print("Password must be at least 8 characters.\n")
            continue
            
        return password

def get_yes_no_input(prompt, default_yes=False):
    """Get yes/no input with validation"""
    while True:
        response = input(prompt).strip().lower()
        if response in ['y', 'yes', '']:
            return True
        elif response in ['n', 'no']:
            return False
        else:
            print("Please enter 'y' or 'n'")

def edit_user_username(cur, user_id, current_username, hall_id):
    """Edit username for a user"""
    print(f"\nEditing username for: {current_username}")
    
    while True:
        new_username = input("New Username: ").strip()
        
        if not new_username:
            print("Username cannot be empty")
            continue
            
        if new_username == current_username:
            print("Username is the same as current. No changes made.")
            return None
            
        # Check if username already exists in this hall
        cur.execute("""
            SELECT id FROM initialscheck 
            WHERE hall_id = %s AND username = %s AND id != %s
        """, (hall_id, new_username, user_id))
        
        if cur.fetchone():
            print(f"Username '{new_username}' already exists in this hall")
            continue
        
        # Confirm change
        print(f"\nChange username from '{current_username}' to '{new_username}'?")
        confirm = get_yes_no_input("Confirm change? (y/n): ")
        
        if confirm:
            cur.execute("""
                UPDATE initialscheck 
                SET username = %s
                WHERE id = %s
            """, (new_username, user_id))
            print(f"Username updated to: {new_username}")
            return new_username
        else:
            print("Username change cancelled.")
            return None

def reset_user_password(cur, user_id, username, make_temporary=True):
    """Reset password for a user - optionally as temporary"""
    print(f"\nResetting password for: {username}")
    
    if make_temporary:
        # Generate temporary password
        temp_password = generate_temp_password()
        print(f"\nGenerated temporary password: {temp_password}")
        print("WARNING: User will be forced to change this password on next login!")
        
        confirm = get_yes_no_input("Use this temporary password? (y/n): ")
        
        if not confirm:
            print("Password reset cancelled.")
            return None
            
        password_hash = hash_password(temp_password)
        temp_flag = True
        password_to_show = temp_password
    else:
        # Let admin set permanent password
        print("\nSetting permanent password for user:")
        password = get_password_input()
        confirm_password = get_password_input("Confirm Password: ")
        
        if password != confirm_password:
            print("Passwords don't match. Operation cancelled.")
            return None
            
        password_hash = hash_password(password)
        temp_flag = False
        password_to_show = password
    
    # Update password
    cur.execute("""
        UPDATE initialscheck 
        SET password_hash = %s, 
            temporary_password = %s,
            last_password_change = NOW()
        WHERE id = %s
    """, (password_hash.decode('utf-8'), temp_flag, user_id))
    
    return password_to_show

def edit_user_permissions(cur, user_id, username, current_permissions):
    """Edit user permissions"""
    print(f"\nEditing permissions for: {username}")
    print("Current permissions:")
    print(f"  • Can check-in packages: {'Yes' if current_permissions['can_checkin'] else 'No'}")
    print(f"  • Can check-out packages: {'Yes' if current_permissions['can_checkout'] else 'No'}")
    print(f"  • Can view other halls: {'Yes' if current_permissions['can_view_other_halls'] else 'No'}")
    print(f"  • Can manage users: {'Yes' if current_permissions['can_manage_users'] else 'No'}")
    print(f"  • Can manage halls: {'Yes' if current_permissions['can_manage_halls'] else 'No'}")
    print(f"  • Account active: {'Yes' if current_permissions['is_active'] else 'No'}")
    
    print("\nSet new permissions:")
    can_checkin = get_yes_no_input("Can check-in packages? (y/n): ")
    can_checkout = get_yes_no_input("Can check-out packages? (y/n): ")
    can_view_other_halls = get_yes_no_input("Can view other halls? (y/n): ")
    can_manage_users = get_yes_no_input("Can manage users? (y/n): ")
    can_manage_halls = get_yes_no_input("Can manage halls? (y/n): ")
    is_active = get_yes_no_input("Account active? (y/n): ")
    
    # Confirm changes
    print(f"\nUpdate permissions for {username}?")
    print(f"  Check-in: {'Yes' if can_checkin else 'No'}")
    print(f"  Check-out: {'Yes' if can_checkout else 'No'}")
    print(f"  View other halls: {'Yes' if can_view_other_halls else 'No'}")
    print(f"  Manage users: {'Yes' if can_manage_users else 'No'}")
    print(f"  Manage halls: {'Yes' if can_manage_halls else 'No'}")
    print(f"  Active: {'Yes' if is_active else 'No'}")
    
    confirm = get_yes_no_input("Confirm permission changes? (y/n): ")
    
    if confirm:
        cur.execute("""
            UPDATE initialscheck 
            SET can_checkin = %s, can_checkout = %s, can_view_other_halls = %s,
                can_manage_users = %s, can_manage_halls = %s, is_active = %s
            WHERE id = %s
        """, (can_checkin, can_checkout, can_view_other_halls,
              can_manage_users, can_manage_halls, is_active, user_id))
        print("Permissions updated successfully!")
        return True
    else:
        print("Permission changes cancelled.")
        return False

def create_new_user(cur, hall_id, hall_code):
    """Create a new user with admin control"""
    print(f"\nCreating new user for {hall_code}:")
    
    # Get username
    while True:
        username = input("Username: ").strip()
        if not username:
            print("Username cannot be empty")
            continue
            
        # Check if username already exists in this hall
        cur.execute("""
            SELECT id FROM initialscheck 
            WHERE hall_id = %s AND username = %s
        """, (hall_id, username))
        
        if cur.fetchone():
            print(f"Username '{username}' already exists in this hall")
            continue
        break
    
    fullname = input("Full Name: ").strip()
    
    while True:
        initials = input("Initials (2-4 letters): ").strip().upper()
        if len(initials) < 2 or len(initials) > 4:
            print("Initials must be 2-4 letters")
            continue
        break
    
    # Ask for title/role
    print("\nSelect role:")
    print("  1. Hall Director (HD) - Full access")
    print("  2. Assistant Hall Director (AHD) - Full access")
    print("  3. Office Assistant (OA) - Limited access")
    print("  4. Custom role")
    
    while True:
        role_choice = input("Enter choice (1-4): ").strip()
        if role_choice == '1':
            title = 'HD'
            break
        elif role_choice == '2':
            title = 'AHD'
            break
        elif role_choice == '3':
            title = 'OA'
            break
        elif role_choice == '4':
            title = input("Custom role name: ").strip().upper()
            break
        else:
            print("Invalid choice. Enter 1-4.")
    
    # Ask for password type
    print("\nPassword type:")
    print("  1. Temporary password (user must change on first login)")
    print("  2. Permanent password (admin sets password)")
    
    while True:
        pass_choice = input("Enter choice (1-2): ").strip()
        if pass_choice == '1':
            password = generate_temp_password()
            temp_password = True
            print(f"\nGenerated temporary password: {password}")
            break
        elif pass_choice == '2':
            password = get_password_input()
            temp_password = False
            break
        else:
            print("Invalid choice. Enter 1 or 2.")
    
    # Set permissions based on role
    if title in ['HD', 'AHD']:
        can_checkin = can_checkout = can_view_other_halls = can_manage_users = can_manage_halls = True
    elif title == 'OA':
        can_checkin = can_checkout = True
        can_view_other_halls = can_manage_users = can_manage_halls = False
    else:
        # For custom roles, ask for permissions
        print("\nSet permissions for custom role:")
        can_checkin = get_yes_no_input("Can check-in packages? (y/n): ")
        can_checkout = get_yes_no_input("Can check-out packages? (y/n): ")
        can_view_other_halls = get_yes_no_input("Can view other halls? (y/n): ")
        can_manage_users = get_yes_no_input("Can manage users? (y/n): ")
        can_manage_halls = get_yes_no_input("Can manage halls? (y/n): ")
    
    # Confirm creation
    print(f"\n" + "=" * 60)
    print("CONFIRM USER CREATION")
    print("=" * 60)
    print(f"Hall: {hall_code}")
    print(f"Username: {username}")
    print(f"Full Name: {fullname}")
    print(f"Initials: {initials}")
    print(f"Title: {title}")
    print(f"Password Type: {'Temporary' if temp_password else 'Permanent'}")
    if temp_password:
        print(f"Temporary Password: {password}")
    print(f"Permissions:")
    print(f"  • Check-in: {'Yes' if can_checkin else 'No'}")
    print(f"  • Check-out: {'Yes' if can_checkout else 'No'}")
    print(f"  • View other halls: {'Yes' if can_view_other_halls else 'No'}")
    print(f"  • Manage users: {'Yes' if can_manage_users else 'No'}")
    print(f"  • Manage halls: {'Yes' if can_manage_halls else 'No'}")
    print("=" * 60)
    
    confirm = get_yes_no_input("\nCreate this user? (y/n): ")
    if not confirm:
        print("Operation cancelled.")
        return None
    
    # Create user
    print("\nCreating user...")
    
    password_hash = hash_password(password)
    
    # Insert user
    sql = """
        INSERT INTO initialscheck 
        (hall_id, username, initials, fullName, title, password_hash,
         can_checkin, can_checkout, can_view_other_halls,
         can_manage_users, can_manage_halls, is_active, temporary_password)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
    """
    
    cur.execute(sql, (hall_id, username, initials, 
                     fullname, title, password_hash.decode('utf-8'),
                     can_checkin, can_checkout, can_view_other_halls,
                     can_manage_users, can_manage_halls, temp_password))
    
    return username, password

def delete_user(cur, user_id, username):
    """Delete a user account"""
    print(f"\nWARNING: You are about to delete user: {username}")
    print("This action cannot be undone!")
    
    confirm = get_yes_no_input("Are you sure you want to delete this user? (y/n): ")
    
    if confirm:
        cur.execute("DELETE FROM initialscheck WHERE id = %s", (user_id,))
        print(f"User '{username}' deleted successfully!")
        return True
    else:
        print("User deletion cancelled.")
        return False

def main():
    print("=" * 80)
    print("ADMIN USER MANAGEMENT SYSTEM")
    print("=" * 80)
    print("\nOptions:")
    print("  1. Create new user")
    print("  2. Edit existing user")
    print("  3. Reset user password")
    print("  4. Edit user permissions")
    print("  5. Delete user")
    print("  6. Exit")
    
    while True:
        choice = input("\nSelect option (1-6): ").strip()
        if choice in ['1', '2', '3', '4', '5', '6']:
            break
        print("Invalid choice. Enter 1-6.")
    
    if choice == '6':
        print("\nExiting...")
        return
    
    try:
        # Connect to database
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor(dictionary=True)
        
        # List existing halls
        halls = list_existing_halls(cur)
        if not halls:
            cur.close()
            conn.close()
            return
        
        # Let user select a hall
        while True:
            try:
                selection = input(f"\nSelect hall number (1-{len(halls)}): ").strip()
                if not selection.isdigit():
                    print("Please enter a number")
                    continue
                    
                selection = int(selection)
                if 1 <= selection <= len(halls):
                    selected_hall = halls[selection - 1]
                    break
                else:
                    print(f"Please enter a number between 1 and {len(halls)}")
            except ValueError:
                print("Invalid input")
        
        print(f"\nSelected Hall: {selected_hall['hall_name']} ({selected_hall['hall_code']})")
        
        # List all users in the hall
        users = list_all_users_with_details(cur, selected_hall['id'])
        
        if not users:
            print(f"No users found in {selected_hall['hall_code']}!")
            if choice != '1':  # Only allow create new user if no users exist
                print("Please use option 1 to create the first user.")
                cur.close()
                conn.close()
                return
        
        result = None
        
        if choice == '1':  # Create new user
            result = create_new_user(cur, selected_hall['id'], selected_hall['hall_code'])
            
            if result:
                username, password = result
                conn.commit()
                print("\n" + "=" * 60)
                print("USER CREATED SUCCESSFULLY!")
                print("=" * 60)
        
        elif choice in ['2', '3', '4', '5']:  # Actions on existing users
            if not users:
                print("No users to edit.")
                cur.close()
                conn.close()
                return
            
            # Let user select which user to edit
            print(f"\nSelect user to {'edit' if choice == '2' else 'reset password for' if choice == '3' else 'edit permissions for' if choice == '4' else 'delete'}:")
            
            for i, user in enumerate(users, 1):
                status = "ACTIVE" if user['is_active'] else "INACTIVE"
                password_status = "TEMP" if user['temporary_password'] else "PERM"
                print(f"{i}. {user['username']} ({user['initials']}) - {user['title']} - {status} - {password_status}")
            
            while True:
                try:
                    user_selection = input(f"\nSelect user (1-{len(users)}): ").strip()
                    if not user_selection.isdigit():
                        print("Please enter a number")
                        continue
                        
                    user_selection = int(user_selection)
                    if 1 <= user_selection <= len(users):
                        selected_user = users[user_selection - 1]
                        break
                    else:
                        print(f"Please enter a number between 1 and {len(users)}")
                except ValueError:
                    print("Invalid input")
            
            if choice == '2':  # Edit user
                print("\nEdit User Options:")
                print("  1. Edit username")
                print("  2. Edit full name")
                print("  3. Edit initials")
                print("  4. Edit title")
                print("  5. Cancel")
                
                while True:
                    edit_choice = input("\nSelect edit option (1-5): ").strip()
                    if edit_choice in ['1', '2', '3', '4', '5']:
                        break
                    print("Invalid choice. Enter 1-5.")
                
                if edit_choice == '1':
                    result = edit_user_username(cur, selected_user['id'], 
                                               selected_user['username'], 
                                               selected_hall['id'])
                elif edit_choice == '2':
                    new_fullname = input(f"New Full Name (current: {selected_user['fullName']}): ").strip()
                    if new_fullname and new_fullname != selected_user['fullName']:
                        cur.execute("UPDATE initialscheck SET fullName = %s WHERE id = %s", 
                                   (new_fullname, selected_user['id']))
                        print("Full name updated!")
                        result = new_fullname
                    else:
                        print("No changes made.")
                elif edit_choice == '3':
                    new_initials = input(f"New Initials (current: {selected_user['initials']}): ").strip().upper()
                    if new_initials and new_initials != selected_user['initials']:
                        if 2 <= len(new_initials) <= 4:
                            cur.execute("UPDATE initialscheck SET initials = %s WHERE id = %s", 
                                       (new_initials, selected_user['id']))
                            print("Initials updated!")
                            result = new_initials
                        else:
                            print("Initials must be 2-4 letters.")
                    else:
                        print("No changes made.")
                elif edit_choice == '4':
                    new_title = input(f"New Title (current: {selected_user['title']}): ").strip().upper()
                    if new_title and new_title != selected_user['title']:
                        cur.execute("UPDATE initialscheck SET title = %s WHERE id = %s", 
                                   (new_title, selected_user['id']))
                        print("Title updated!")
                        result = new_title
                    else:
                        print("No changes made.")
                
                if result and edit_choice != '5':
                    conn.commit()
            
            elif choice == '3':  # Reset password
                print("\nPassword Reset Options:")
                print("  1. Set temporary password (user must change on next login)")
                print("  2. Set permanent password")
                print("  3. Cancel")
                
                while True:
                    pass_choice = input("\nSelect option (1-3): ").strip()
                    if pass_choice in ['1', '2', '3']:
                        break
                    print("Invalid choice. Enter 1-3.")
                
                if pass_choice in ['1', '2']:
                    make_temporary = (pass_choice == '1')
                    result_password = reset_user_password(cur, selected_user['id'], 
                                                         selected_user['username'], 
                                                         make_temporary)
                    if result_password:
                        conn.commit()
                        print("\n" + "=" * 60)
                        print("PASSWORD RESET SUCCESSFUL!")
                        print("=" * 60)
                        if make_temporary:
                            print(f"Temporary password: {result_password}")
                        else:
                            print(f"New permanent password set")
                        result = selected_user['username']
            
            elif choice == '4':  # Edit permissions
                current_perms = {
                    'can_checkin': selected_user['can_checkin'],
                    'can_checkout': selected_user['can_checkout'],
                    'can_view_other_halls': selected_user['can_view_other_halls'],
                    'can_manage_users': selected_user['can_manage_users'],
                    'can_manage_halls': selected_user['can_manage_halls'],
                    'is_active': selected_user['is_active']
                }
                
                result = edit_user_permissions(cur, selected_user['id'], 
                                              selected_user['username'], 
                                              current_perms)
                if result:
                    conn.commit()
                    print("\nPermissions updated successfully!")
            
            elif choice == '5':  # Delete user
                result = delete_user(cur, selected_user['id'], selected_user['username'])
                if result:
                    conn.commit()
                    print("\nUser deleted successfully!")
        
        # Display final success message
        if result:
            print(f"\n{'='*60}")
            print("OPERATION COMPLETED SUCCESSFULLY!")
            print(f"{'='*60}")
            
            if choice == '1':  # New user created
                username, password = result
                print(f"\nSuccessfully created user: {username}")
                print(f"   Hall Code: {selected_hall['hall_code']}")
                print(f"   Hall Name: {selected_hall['hall_name']}")
                
                print(f"\nLogin Information:")
                print(f"   • Hall Code: {selected_hall['hall_code']}")
                print(f"   • Username: {username}")
                print(f"   • Password: {password}")
                
                print(f"\nNext Steps:")
                print(f"   1. Go to http://127.0.0.1:5300")
                print(f"   2. Login with the credentials above")
            
                print(f"\nIMPORTANT:")
                print(f"   • Store credentials securely")
                print(f"   • Consider deleting this script after use")
            
            print(f"\n{'='*60}")
            print("Ready to login!")
            print(f"{'='*60}")
        else:
            print("\nOperation was cancelled or no changes were made.")
        
    except mysql.connector.Error as e:
        print(f"\nDATABASE ERROR: {e}")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            cur.close()
            conn.close()
        except:
            pass
    
    print("\nScript complete.")

if __name__ == "__main__":
    main()