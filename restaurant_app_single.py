#!/usr/bin/env python3
# Single-file Restaurant Management Application

import customtkinter as ctk
from tkinter import ttk, messagebox, simpledialog
import sqlite3
import bcrypt
import os
import sys
import datetime
from tkcalendar import DateEntry # Ensure tkcalendar is installed

# --- Constants and Path Handling ---

# Determine base directory for resources (works in development and PyInstaller)
if hasattr(sys, 	"_MEIPASS	"):  # PyInstaller creates a temp folder and stores path in _MEIPASS
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

DATABASE_NAME = os.path.join(base_dir, "restaurant_management.db")
ASSETS_PATH = os.path.join(base_dir, "assets") # Define assets path if needed

print(f"Base directory determined as: {base_dir}")
print(f"Database path set to: {DATABASE_NAME}")

# --- Database Management Code (from db/database_manager.py) ---

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        conn.row_factory = sqlite3.Row # Access columns by name
        print(f"Successfully connected to database: {DATABASE_NAME}")
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to database {DATABASE_NAME}: {e}")
        messagebox.showerror("Database Connection Error", f"Could not connect to the database: {e}\nDatabase expected at: {DATABASE_NAME}")
        sys.exit(1) # Exit if DB connection fails

def hash_password(password: str) -> bytes:
    """Hashes a password using bcrypt."""
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed_password

def check_password(password: str, hashed_password: bytes) -> bool:
    """Checks if the provided password matches the hashed password."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password)

def add_user(username: str, password: str, role: str, full_name: str = "") -> bool:
    """Adds a new user to the Users table. Returns True on success, False otherwise."""
    hashed_pw = hash_password(password)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Users (username, password_hash, role, full_name)
            VALUES (?, ?, ?, ?)
        """, (username, hashed_pw, role, full_name))
        conn.commit()
        print(f"User {username} added successfully with role {role}.")
        return True
    except sqlite3.IntegrityError: # Handles UNIQUE constraint violation for username
        print(f"Error: Username {username} already exists.")
        return False
    except sqlite3.Error as e:
        print(f"Database error while adding user: {e}")
        return False
    finally:
        if conn:
            conn.close()

def verify_user(username: str, password: str) -> tuple[bool, str | None, int | None]: # Return user_id as int
    """Verifies user credentials. Returns (success_status, user_role, user_id)."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, password_hash, role FROM Users WHERE username = ? AND is_active = 1", (username,))
        user_record = cursor.fetchone()

        if user_record:
            if check_password(password, user_record["password_hash"]):
                print(f"User {username} verified successfully. Role: {user_record[	"role	"]}")
                return True, user_record["role"], int(user_record["user_id"]) # Cast user_id to int
            else:
                print(f"Invalid password for user {username}.")
                return False, None, None
        else:
            print(f"User {username} not found or is inactive.")
            return False, None, None
    except sqlite3.Error as e:
        print(f"Database error during user verification: {e}")
        return False, None, None
    finally:
        if conn:
            conn.close()

# ... (Add ALL other functions from database_manager.py here) ...
# Make sure to include functions for categories, items, tables, orders, inventory, reports etc.

# --- UI Code (from ui/*.py files) ---

# --- LoginFrame Code (from ui/login_view.py) ---
class LoginFrame(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        
        # Configure grid layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # Create centered login form
        login_container = ctk.CTkFrame(self)
        login_container.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        
        # Configure login container grid
        login_container.grid_columnconfigure(0, weight=1)
        login_container.grid_rowconfigure(0, weight=1)
        login_container.grid_rowconfigure(1, weight=0)
        login_container.grid_rowconfigure(2, weight=0)
        login_container.grid_rowconfigure(3, weight=0)
        login_container.grid_rowconfigure(4, weight=0)
        
        # Title
        title_label = ctk.CTkLabel(login_container, text="Restaurant Management System", 
                                  font=ctk.CTkFont(size=24, weight="bold"))
        title_label.grid(row=0, column=0, padx=20, pady=(40, 20), sticky="s")
        
        # Username
        self.username_var = ctk.StringVar()
        username_label = ctk.CTkLabel(login_container, text="Username:")
        username_label.grid(row=1, column=0, padx=20, pady=(20, 5), sticky="sw")
        username_entry = ctk.CTkEntry(login_container, width=300, textvariable=self.username_var)
        username_entry.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="n")
        
        # Password
        self.password_var = ctk.StringVar()
        password_label = ctk.CTkLabel(login_container, text="Password:")
        password_label.grid(row=3, column=0, padx=20, pady=(0, 5), sticky="sw")
        password_entry = ctk.CTkEntry(login_container, width=300, textvariable=self.password_var, show="*")
        password_entry.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="n")
        
        # Login button
        login_button = ctk.CTkButton(login_container, text="Login", command=self.login, width=200)
        login_button.grid(row=5, column=0, padx=20, pady=(0, 40))
        
        # Set focus to username entry
        username_entry.focus_set()
        
        # Bind Enter key to login
        self.bind("<Return>", lambda event: self.login())
        username_entry.bind("<Return>", lambda event: self.login())
        password_entry.bind("<Return>", lambda event: self.login())
        
    def login(self):
        username = self.username_var.get()
        password = self.password_var.get()
        
        if not username or not password:
            messagebox.showerror("Login Error", "Username and password cannot be empty.")
            return
        
        success, role, user_id = verify_user(username, password)
        if success:
            self.controller.login_successful(username, role, user_id)
        else:
            messagebox.showerror("Login Error", "Invalid username or password.")

# --- AdminDashboard Code (from ui/admin_dashboard_view.py) ---
# ... (Paste the AdminDashboard class code here, remove imports) ...

# --- WaiterDashboard Code (from ui/waiter_dashboard_view.py) ---
# ... (Paste the WaiterDashboard class code here, remove imports) ...

# --- OrderCreationFrame Code (from ui/order_creation_view.py) ---
# ... (Paste the OrderCreationFrame class code here, remove imports) ...

# --- CashierDashboard Code (from ui/cashier_dashboard_view.py) ---
# ... (Paste the CashierDashboard class code here, remove imports) ...


# --- Main Application Code (from main.py) ---
class RestaurantAppSingle(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Restaurant Management System")
        self.geometry("900x700") # Increased default size
        self.current_user_role = None
        self.current_user_id = None
        self.current_username = None

        # Check if database exists and has users, if not, add default users
        self.initialize_database_and_users()

        # Container for frames
        self.container = ctk.CTkFrame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {} # Store instances of frames
        self.show_login_frame()

    def initialize_database_and_users(self):
        """Checks for DB and initial users, adds them if not present."""
        if not os.path.exists(DATABASE_NAME):
            # Try to run setup_database.py if it exists
            setup_script = os.path.join(base_dir, "setup_database.py")
            if os.path.exists(setup_script):
                try:
                    print("Database not found. Attempting to run setup_database.py...")
                    # Use subprocess to run setup script to avoid import issues
                    import subprocess
                    subprocess.run([sys.executable, setup_script], check=True, cwd=base_dir)
                    print("setup_database.py executed successfully.")
                    # Verify DB creation
                    if not os.path.exists(DATABASE_NAME):
                         raise Exception("setup_database.py ran but did not create the database file.")
                except Exception as setup_e:
                    messagebox.showerror("Database Setup Error", f"Failed to automatically create database using setup_database.py: {setup_e}")
                    self.quit()
                    return 
            else:
                messagebox.showerror("Database Error", f"Database file not found at {DATABASE_NAME} and setup_database.py is missing. Cannot initialize.")
                self.quit()
                return

        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Users")
            user_count = cursor.fetchone()[0]
            if user_count == 0:
                print("No users found. Adding default users...")
                add_user("admin", "admin123", "Admin", "Administrator")
                add_user("waiter1", "waiter123", "Waiter", "John Doe")
                add_user("cashier1", "cashier123", "Cashier", "Jane Smith")
                print("Default users added.")
            else:
                print(f"{user_count} users found in the database.")
        except Exception as e:
            messagebox.showerror("Database Error", f"Error initializing users: {e}")
            print(f"Error initializing users: {e}")
        finally:
            if conn:
                conn.close()

    def show_frame(self, page_name, *args):
        """Shows a frame for the given page name. Args are passed to the frame constructor."""
        for widget in self.container.winfo_children():
            widget.destroy()

        frame_class = None
        # Map page names to classes defined in this file
        if page_name == "LoginFrame":
            frame_class = LoginFrame
        elif page_name == "AdminDashboard":
            frame_class = AdminDashboard # Assumes AdminDashboard class is defined above
        elif page_name == "WaiterDashboard":
            frame_class = WaiterDashboard # Assumes WaiterDashboard class is defined above
        elif page_name == "OrderCreationFrame":
            frame_class = OrderCreationFrame # Assumes OrderCreationFrame class is defined above
        elif page_name == "CashierDashboard":
            frame_class = CashierDashboard # Assumes CashierDashboard class is defined above
        else:
            raise ValueError(f"Unknown frame: {page_name}")

        if frame_class:
            frame = frame_class(self.container, self, *args) 
            self.frames[page_name] = frame 
            frame.grid(row=0, column=0, sticky="nsew")
            frame.tkraise()

    def show_login_frame(self):
        self.show_frame("LoginFrame")

    def login_successful(self, username, user_role, user_id):
        self.current_username = username
        self.current_user_role = user_role
        self.current_user_id = user_id
        messagebox.showinfo("Login Success", f"Welcome {username}! Role: {user_role}")
        if user_role == "Admin":
            self.show_frame("AdminDashboard")
        elif user_role == "Waiter":
            self.show_frame("WaiterDashboard")
        elif user_role == "Cashier":
            self.show_frame("CashierDashboard")
        else:
            messagebox.showerror("Login Error", "Unknown user role.")
            self.show_login_frame()
    
    def get_current_frame_instance(self, frame_name):
        return self.frames.get(frame_name)

# --- Main Execution ---
if __name__ == "__main__":
    # Ensure DB exists or can be created
    if not os.path.exists(DATABASE_NAME):
        setup_script = os.path.join(base_dir, "setup_database.py")
        if os.path.exists(setup_script):
             print(f"Database not found at {DATABASE_NAME}. Please run setup_database.py first or ensure it is in the same directory as this script.")
             # Attempt to run setup script automatically during first run
             try:
                 import subprocess
                 subprocess.run([sys.executable, setup_script], check=True, cwd=base_dir)
                 print("Database setup script executed.")
                 if not os.path.exists(DATABASE_NAME):
                     raise Exception("Database file still not found after running setup script.")
             except Exception as e:
                 messagebox.showerror("Database Setup Failed", f"Could not automatically run setup_database.py: {e}")
                 sys.exit(1)
        else:
             messagebox.showerror("Database Missing", f"Database file not found at {DATABASE_NAME} and setup_database.py is missing.")
             sys.exit(1)

    # Run the application
    app = RestaurantAppSingle()
    app.mainloop()

# --- Placeholder for remaining DB functions and UI classes ---
# Remember to paste the full code for:
# - All remaining functions from database_manager.py
# - AdminDashboard class from admin_dashboard_view.py
# - WaiterDashboard class from waiter_dashboard_view.py
# - OrderCreationFrame class from order_creation_view.py
# - CashierDashboard class from cashier_dashboard_view.py
# Ensure all internal imports within these classes are removed or adjusted.

