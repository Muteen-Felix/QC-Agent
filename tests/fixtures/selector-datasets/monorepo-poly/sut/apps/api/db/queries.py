def find_item(conn, item_id: int):
    return conn.execute("SELECT id, name FROM items WHERE id = ?", (item_id,)).fetchone()
