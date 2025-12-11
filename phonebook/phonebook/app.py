# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
import csv
import io

app = Flask(__name__)
CORS(app)

DB_PATH = "contacts.db"


def init_db():
    if os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            is_favorite INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE contact_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            method_type TEXT NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def get_contact_with_methods(cursor, contact_row):
    contact_id, name, address, is_favorite = contact_row
    methods = cursor.execute(
        "SELECT method_type, value FROM contact_methods WHERE contact_id = ?",
        (contact_id,)
    ).fetchall()
    return {
        "id": contact_id,
        "name": name,
        "address": address,
        "is_favorite": bool(is_favorite),
        "methods": [{"type": m[0], "value": m[1]} for m in methods]
    }


@app.route('/contacts', methods=['GET'])
def get_contacts():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, name, address, is_favorite FROM contacts").fetchall()
    result = [get_contact_with_methods(cursor, row) for row in rows]
    conn.close()
    return jsonify(result)


@app.route('/contacts/favorites', methods=['GET'])
def get_favorites():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, name, address, is_favorite FROM contacts WHERE is_favorite = 1").fetchall()
    result = [get_contact_with_methods(cursor, row) for row in rows]
    conn.close()
    return jsonify(result)


@app.route('/contacts', methods=['POST'])
def add_contact():
    data = request.get_json()
    if not data:
        return jsonify({"error": "请求体不能为空"}), 400
    name = data.get('name', '').strip()
    raw_address = data.get('address')
    address = str(raw_address).strip() if raw_address is not None else None
    methods = data.get('methods', [])
    if not name:
        return jsonify({"error": "姓名不能为空"}), 400
    if not isinstance(methods, list) or len(methods) == 0:
        return jsonify({"error": "至少需要一个联系方式"}), 400
    for m in methods:
        if not m.get('type') or not m.get('value'):
            return jsonify({"error": "每个联系方式必须包含 type 和 value"}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO contacts (name, address) VALUES (?, ?)", (name, address))
    contact_id = cursor.lastrowid
    for m in methods:
        cursor.execute(
            "INSERT INTO contact_methods (contact_id, method_type, value) VALUES (?, ?, ?)",
            (contact_id, m['type'], m['value'])
        )
    conn.commit()
    conn.close()
    return jsonify({
        "id": contact_id,
        "name": name,
        "address": address,
        "is_favorite": False,
        "methods": methods
    }), 201


@app.route('/contacts/<int:contact_id>', methods=['PUT'])
def update_contact(contact_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "请求体不能为空"}), 400
    name = data.get('name', '').strip()
    raw_address = data.get('address')
    address = str(raw_address).strip() if raw_address is not None else None
    methods = data.get('methods', [])
    if not name:
        return jsonify({"error": "姓名不能为空"}), 400
    if not isinstance(methods, list):
        return jsonify({"error": "methods 必须是数组"}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    exists = cursor.execute("SELECT 1 FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not exists:
        conn.close()
        return jsonify({"error": "联系人不存在"}), 404

    cursor.execute("UPDATE contacts SET name = ?, address = ? WHERE id = ?", (name, address, contact_id))
    cursor.execute("DELETE FROM contact_methods WHERE contact_id = ?", (contact_id,))
    for m in methods:
        if m.get('type') and m.get('value'):
            cursor.execute(
                "INSERT INTO contact_methods (contact_id, method_type, value) VALUES (?, ?, ?)",
                (contact_id, m['type'], m['value'])
            )
    conn.commit()
    conn.close()
    return jsonify({
        "id": contact_id,
        "name": name,
        "address": address,
        "is_favorite": bool(exists),
        "methods": methods
    })


@app.route('/contacts/<int:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    if not deleted:
        return jsonify({"error": "联系人不存在"}), 404
    return jsonify({"message": "删除成功"}), 200


@app.route('/contacts/<int:contact_id>/favorite', methods=['PUT'])
def toggle_favorite(contact_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT is_favorite FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "联系人不存在"}), 404
    new_status = 0 if row[0] == 1 else 1
    cursor.execute("UPDATE contacts SET is_favorite = ? WHERE id = ?", (new_status, contact_id))
    conn.commit()
    conn.close()
    return jsonify({"is_favorite": bool(new_status)})


# ✅ 新增：CSV 导入接口
@app.route('/contacts/import', methods=['POST'])
def import_contacts():
    if 'file' not in request.files:
        return jsonify({"error": "未提供文件"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "文件名为空"}), 400
    if not file.filename.lower().endswith('.csv'):
        return jsonify({"error": "仅支持 .csv 文件"}), 400

    try:
        # 自动处理 UTF-8 with BOM
        content = file.read()
        if content.startswith(b'\xef\xbb\xbf'):
            text = content[3:].decode('utf-8')
        else:
            text = content.decode('utf-8')

        reader = csv.DictReader(io.StringIO(text))
        required_fields = {'姓名', '电话', '邮箱', '住址'}
        if not required_fields.issubset(set(reader.fieldnames or [])):
            return jsonify({"error": f"缺少必要列：{required_fields}"}), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        count = 0
        for row in reader:
            name = (row.get('姓名') or '').strip()
            if not name:
                continue  # 跳过空行
            phone = (row.get('电话') or '').strip()
            email = (row.get('邮箱') or '').strip()
            address = (row.get('住址') or '').strip()

            cursor.execute("INSERT INTO contacts (name, address) VALUES (?, ?)", (name, address or None))
            contact_id = cursor.lastrowid
            if phone:
                cursor.execute("INSERT INTO contact_methods (contact_id, method_type, value) VALUES (?, ?, ?)",
                               (contact_id, 'phone', phone))
            if email:
                cursor.execute("INSERT INTO contact_methods (contact_id, method_type, value) VALUES (?, ?, ?)",
                               (contact_id, 'email', email))
            if address:
                cursor.execute("INSERT INTO contact_methods (contact_id, method_type, value) VALUES (?, ?, ?)",
                               (contact_id, 'address', address))
            count += 1

        conn.commit()
        conn.close()
        return jsonify({"message": f"成功导入 {count} 条联系人"}), 200

    except Exception as e:
        print("导入错误:", e)
        return jsonify({"error": f"文件解析失败：{str(e)}"}), 400


if __name__ == '__main__':
    init_db()
    print("✅ 数据库初始化完成")
    print("🚀 后端服务启动中... 访问 http://127.0.0.1:5000/contacts 查看数据")
    app.run(host='0.0.0.0', port=5000, debug=True)