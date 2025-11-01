#!/usr/bin/env python3
import sqlite3

DATABASE_NAME = "restaurant_management.db"

def create_connection(db_file):
    """ create a database connection to the SQLite database specified by db_file """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        print(f"SQLite version: {sqlite3.sqlite_version}")
        print(f"Successfully connected to {db_file}")
    except sqlite3.Error as e:
        print(e)
    return conn

def create_table(conn, create_table_sql):
    """ create a table from the create_table_sql statement """
    try:
        c = conn.cursor()
        c.execute(create_table_sql)
    except sqlite3.Error as e:
        print(e)

def main():
    sql_create_users_table = """
    CREATE TABLE IF NOT EXISTS Users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('Admin', 'Waiter', 'Cashier')),
        full_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1 CHECK(is_active IN (0, 1))
    );
    """

    sql_create_categories_table = """
    CREATE TABLE IF NOT EXISTS Categories (
        category_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT
    );
    """

    sql_create_menu_items_table = """
    CREATE TABLE IF NOT EXISTS MenuItems (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        price REAL NOT NULL CHECK(price >= 0),
        category_id INTEGER NOT NULL,
        image_path TEXT,
        current_stock INTEGER DEFAULT 0 CHECK(current_stock >= 0),
        is_available INTEGER DEFAULT 1 CHECK(is_available IN (0, 1)),
        FOREIGN KEY (category_id) REFERENCES Categories (category_id) ON DELETE CASCADE
    );
    """

    sql_create_tables_table = """
    CREATE TABLE IF NOT EXISTS Tables (
        table_id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_number TEXT NOT NULL UNIQUE,
        capacity INTEGER CHECK(capacity > 0),
        status TEXT DEFAULT 'Available' CHECK(status IN ('Available', 'Occupied', 'Reserved', 'Needs Cleaning'))
    );
    """

    sql_create_orders_table = """
    CREATE TABLE IF NOT EXISTS Orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_id INTEGER NOT NULL,
        user_id_waiter INTEGER,
        user_id_cashier INTEGER,
        order_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'Active' CHECK(status IN ('Active', 'Completed', 'Paid', 'Cancelled')),
        total_amount REAL DEFAULT 0.0 CHECK(total_amount >= 0),
        payment_time TIMESTAMP,
        payment_method TEXT,
        FOREIGN KEY (table_id) REFERENCES Tables (table_id),
        FOREIGN KEY (user_id_waiter) REFERENCES Users (user_id),
        FOREIGN KEY (user_id_cashier) REFERENCES Users (user_id)
    );
    """

    sql_create_order_items_table = """
    CREATE TABLE IF NOT EXISTS OrderItems (
        order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        price_at_order REAL NOT NULL CHECK(price_at_order >= 0),
        subtotal REAL NOT NULL CHECK(subtotal >= 0),
        FOREIGN KEY (order_id) REFERENCES Orders (order_id) ON DELETE CASCADE,
        FOREIGN KEY (item_id) REFERENCES MenuItems (item_id) ON DELETE RESTRICT
    );
    """

    sql_create_inventory_log_table = """
    CREATE TABLE IF NOT EXISTS InventoryLog (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        change_quantity INTEGER NOT NULL,
        new_stock_level INTEGER NOT NULL CHECK(new_stock_level >= 0),
        reason TEXT NOT NULL CHECK(reason IN ('Sale', 'Manual Stock Entry', 'Spoilage', 'Correction', 'Initial Stock')),
        order_item_id INTEGER,
        user_id_admin INTEGER,
        log_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES MenuItems (item_id) ON DELETE CASCADE,
        FOREIGN KEY (order_item_id) REFERENCES OrderItems (order_item_id) ON DELETE SET NULL,
        FOREIGN KEY (user_id_admin) REFERENCES Users (user_id) ON DELETE SET NULL
    );
    """

    # create a database connection
    conn = create_connection(DATABASE_NAME)

    # create tables
    if conn is not None:
        create_table(conn, sql_create_users_table)
        create_table(conn, sql_create_categories_table)
        create_table(conn, sql_create_menu_items_table)
        create_table(conn, sql_create_tables_table)
        create_table(conn, sql_create_orders_table)
        create_table(conn, sql_create_order_items_table)
        create_table(conn, sql_create_inventory_log_table)
        print(f"All tables created successfully in {DATABASE_NAME}.")
        conn.close()
    else:
        print("Error! cannot create the database connection.")

if __name__ == '__main__':
    main()

