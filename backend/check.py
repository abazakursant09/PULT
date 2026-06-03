import sqlite3
conn = sqlite3.connect('business_pult.db')
cursor = conn.execute("SELECT COUNT(*) FROM review_responses WHERE product_id LIKE '3e14c729%'")
print('Найдено:', cursor.fetchone()[0])
conn.close()