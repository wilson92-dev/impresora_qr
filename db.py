import sqlite3
import os

DB_NAME = os.path.join(os.path.dirname(__file__), 'imprimeya.db')

def init_db():
    """Inicializa la base de datos SQLite de forma segura"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedidos (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            paginas INTEGER,
            copias INTEGER,
            modo TEXT,
            total REAL,
            filepath TEXT,
            estado TEXT,
            timestamp REAL
        )
    ''')
    conn.commit()
    conn.close()

def guardar_pedido(pedido):
    """Inserta o actualiza un pedido"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO pedidos (id, original_name, paginas, copias, modo, total, filepath, estado, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        pedido['id'],
        pedido['original_name'],
        pedido['paginas'],
        pedido['copias'],
        pedido['modo'],
        pedido['total'],
        pedido['filepath'],
        pedido['estado'],
        pedido['timestamp']
    ))
    conn.commit()
    conn.close()

def obtener_todos_los_pedidos():
    """Devuelve todos los pedidos en formato de diccionario"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM pedidos')
    rows = cursor.fetchall()
    conn.close()
    
    pedidos = {}
    for row in rows:
        pedidos[row['id']] = dict(row)
    return pedidos