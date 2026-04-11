import os
import io
import uuid
import psycopg2
from psycopg2.errors import UndefinedTable
import psycopg2.extras
from flask import Flask, request, render_template, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from helpers import apology, login_required
from flask_mail import Mail, Message
from dotenv import load_dotenv
from PIL import Image
from supabase import create_client, Client
from flask import jsonify
import unicodedata
import re

# 1. Cargar las variables de entorno
load_dotenv()

app = Flask(__name__)

# 2. Configuración de Flask y Correo usando variables de entorno
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
mail = Mail(app)

app.secret_key = os.getenv("SECRET_KEY")

# 3. Cliente de Supabase (Solo para Storage/Imágenes)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 4. Función Helper para conectarse a Postgres
def get_db_connection():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    return conn

# ========================================================
# CREACIÓN AUTOMÁTICA DEL ADMINISTRADOR
# ========================================================
with app.app_context():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Revisamos si ya existe el administrador (ID 1)
        cursor.execute("SELECT id FROM users WHERE id = 1")
        admin_exists = cursor.fetchone()
        
        if not admin_exists:
            print("🔧 Creando usuario administrador por defecto...")
            
            # Datos de tu administrador
            admin_name = "Admin"
            admin_last_name = "Macon"
            admin_mail = "maconferreteria@gmail.com"
            admin_password = "28Febrerojc"
            
            # Insertamos forzando el ID = 1 (Sin la columna money)
            cursor.execute("""
                INSERT INTO users (id, name, last_name, mail, hash) 
                VALUES (1, %s, %s, %s, %s)
            """, (admin_name, admin_last_name, admin_mail, generate_password_hash(admin_password)))
            
            conn.commit()
            print("✅ Administrador creado exitosamente.")
            
        conn.close()
    except UndefinedTable:
        print("⚠️ Advertencia: Las tablas aún no existen. Ejecuta el código SQL en Supabase primero.")
        conn.rollback()
    except Exception as e:
        print(f"⚠️ Error al intentar crear el admin: {e}")
        conn.rollback()
# ========================================================

# Funciones de utilidad para usuarios
def get_user_name(user_id):
    if user_id is None:
        return None
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT name FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return user["name"]
    return None


@app.route("/")
def index():
    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 1. Obtener los 4 productos más recientes (los últimos agregados)
    cursor.execute('''
        SELECT p.*, 
               EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
        FROM products p
        ORDER BY p.id DESC
        LIMIT 4
    ''')
    recent_products = cursor.fetchall()

    # 2. Lista completa de tus categorías
    todas_las_categorias = [
        {"id": 1, "nombre": "Herramientas"},
        {"id": 2, "nombre": "Herraje"},
        {"id": 3, "nombre": "Plomería"},
        {"id": 4, "nombre": "Electricidad"},
        {"id": 5, "nombre": "Iluminación"},
        {"id": 6, "nombre": "Grifería"},
        {"id": 7, "nombre": "Pintura"},
        {"id": 8, "nombre": "Hogar"}
    ]
    
    categorias_con_productos = []
    
    # 3. Revisar una por una si tienen productos
    for cat in todas_las_categorias:
        cursor.execute('''
            SELECT p.*, 
                   EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
            FROM products p
            WHERE p.id_category = %s
            ORDER BY RANDOM()
            LIMIT 4
        ''', (cat["id"],))
        prods = cursor.fetchall()
        
        # MAGIA AQUÍ: Solo agregamos la categoría a la pantalla si encontró al menos 1 producto
        if prods:
            categorias_con_productos.append({
                "id": cat["id"],
                "nombre": cat["nombre"],
                "productos": prods
            })

    conn.close()

    return render_template("index.html", 
                           user_logged_in=user_logged_in, 
                           user_name=user_name,
                           recent_products=recent_products,
                           categorias_con_productos=categorias_con_productos)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("nombre")
        last_name = request.form.get("apellido")
        email = request.form.get("correo")
        password = request.form.get("password")
        confirm = request.form.get("confirmation")

        if not name or not last_name or not email or not password or not confirm:
            return render_template("register.html", error="Debe proporcionar todos los campos")
        elif password != confirm:
            return render_template("register.html", error="Las contraseñas no coinciden")
        else:
            msg = Message("Message from your site", sender=os.getenv("MAIL_USERNAME"), recipients=[email])
            msg.body = "Se pudo registrar exitosamente."
            msg.html = render_template('email.html', name=name, email=email)
            mail.send(msg)
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # Insertar y obtener el ID en un solo paso (Sin money)
            cursor.execute("""
                INSERT INTO users (name, last_name, mail, hash) 
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (name, last_name, email, generate_password_hash(password)))
            
            new_user = cursor.fetchone()
            conn.commit()
            conn.close()

            session["user_id"] = new_user["id"]
            return redirect("/")
    else:
        return render_template("register.html")

@app.route('/registrarse')
def registrarse():
    return redirect("/register")

@app.route("/login", methods=["GET", "POST"])
def login():
    session.clear()
    if request.method == "POST":
        correo = request.form.get("correo")
        contraseña = request.form.get("contraseña")

        if not correo:
            return render_template("index.html", error="Debe proporcionar el correo electrónico")
        elif not contraseña:
            return render_template("index.html", error="Debe proporcionar la contraseña")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM users WHERE mail = %s", (correo,))
        user = cursor.fetchone()
        conn.close()

        if user is None or not check_password_hash(user["hash"], contraseña):
            return render_template("index.html", error="Correo y/o contraseña inválidos")

        session["user_id"] = user["id"]

        if session["user_id"] == 1:
            return redirect("/admin")

        return redirect("/")
    else:
        return render_template("index.html")

@app.route("/catalogo", methods=["GET", "POST"])
def catalogo():
    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))
    return render_template("catalogpdf.html", user_logged_in=user_logged_in, user_name=user_name)

@app.route("/send", methods=["GET", "POST"])
def send():
    if request.method == "POST":
        name = request.form.get("nombre")
        price = request.form.get("price")
        description = request.form.get("description")
        category = request.form.get("category")
        
        if not name or not price or not description or not category:
            return render_template("apology.html")

        # --- LÓGICA MULTI-IMAGEN (Intacta) ---
        files = request.files.getlist("imagenes_producto")
        manual_url = request.form.get("url")
        urls_subidas = []

        for file in files:
            if file and file.filename != '':
                try:
                    img = Image.open(file)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    
                    buffer_webp = io.BytesIO()
                    img.save(buffer_webp, format="WEBP", quality=80)
                    file_bytes = buffer_webp.getvalue() 
                    file_name = f"{uuid.uuid4().hex}.webp"
                    
                    supabase.storage.from_("productos").upload(
                        path=file_name, file=file_bytes, file_options={"content-type": "image/webp"}
                    )
                    public_url = supabase.storage.from_("productos").get_public_url(file_name)
                    urls_subidas.append(public_url)
                except Exception as e:
                    print(f"Error procesando imagen: {e}")

        main_url = urls_subidas[0] if urls_subidas else (manual_url if manual_url else "")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. Insertar producto
        cursor.execute("""
            INSERT INTO products (name, price, description, id_category, url) 
            VALUES(%s, %s, %s, %s, %s) RETURNING id
        """, (name, price, description, category, main_url))
        product_id = cursor.fetchone()["id"]

        # 2. Insertar el resto de las imágenes
        if len(urls_subidas) > 1:
            for extra_url in urls_subidas[1:]:
                cursor.execute("""
                    INSERT INTO product_images (product_id, image_url) 
                    VALUES (%s, %s)
                """, (product_id, extra_url))

        # 3. Guardar Especificaciones
        for key in request.form:
            if key.startswith("attribute") and request.form[key].strip():
                attribute_num = key.replace("attribute", "")
                attribute = request.form[key].strip()
                value = request.form.get("value" + attribute_num, "").strip()
                cursor.execute("""
                    INSERT INTO product_specifications (product_id, attribute, value) 
                    VALUES (%s, %s, %s)
                """, (product_id, attribute, value))

        # 4. NUEVO: Guardar Variaciones Agrupadas por Tipo
        # Buscamos todos los índices de tipos de variación creados
        variation_indices = []
        for key in request.form:
            if key.startswith("var_type_"):
                variation_indices.append(key.split("_")[-1])

        for idx in variation_indices:
            var_type = request.form.get(f"var_type_{idx}", "Opción").strip()
            # Iteramos buscando las opciones pertenecientes a este tipo (idx)
            for key in request.form:
                if key.startswith(f"var_name_{idx}_"):
                    opt_idx = key.split("_")[-1]
                    var_name = request.form.get(key).strip()
                    # Si no hay precio, usamos 0 o el precio base. Usamos 0 por defecto.
                    var_price = request.form.get(f"var_price_{idx}_{opt_idx}")
                    var_price = float(var_price) if var_price else 0.0

                    if var_name:
                        cursor.execute("""
                            INSERT INTO product_variations (product_id, type, name, price) 
                            VALUES (%s, %s, %s, %s)
                        """, (product_id, var_type, var_name, var_price))

        conn.commit()
        conn.close()

        return render_template("send.html")
    else:
        if session.get("user_id") is None:
            return redirect("/login")
        if session["user_id"] == 1:
            return render_template("send.html")
        return redirect("/")



# --- NUEVA RUTA API PARA EL MODAL (QUICK VIEW) ---
@app.route('/api/product_quickview/<int:product_id>')
def api_product_quickview(product_id):
    """Retorna los datos de un producto y sus variaciones en formato JSON para el Modal."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Obtener el producto
    cursor.execute("SELECT id, name, price, description, url FROM products WHERE id = %s", (product_id,))
    prod = cursor.fetchone()
    
    if not prod:
        conn.close()
        return jsonify({"success": False, "error": "Producto no encontrado"}), 404

    # Obtener variaciones
    cursor.execute("SELECT type, name, price FROM product_variations WHERE product_id = %s", (product_id,))
    variations = cursor.fetchall()
    conn.close()

    vars_list = [{"type": v["type"], "name": v["name"], "price": float(v["price"])} for v in variations]

    return jsonify({
        "success": True,
        "product": {
            "id": prod["id"],
            "name": prod["name"],
            "price": float(prod["price"]),
            "description": prod["description"],
            "image": prod["url"],
            "has_variations": len(vars_list) > 0
        },
        "variations": vars_list
    })






@app.route('/admin')
def admin():
    if session.get("user_id") is None:
        return redirect("/login")
    if session["user_id"] == 1:
        return render_template("admin.html")
    return redirect("/")

@app.route('/quienes')
def quienes():
    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))
    return render_template("quienes.html", user_logged_in=user_logged_in, user_name=user_name)

@app.route('/contacto')
def contacto():
    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))
    return render_template("contacto.html", user_logged_in=user_logged_in, user_name=user_name)

@app.route("/products", defaults={'page': 1})
@app.route("/products/<int:page>")
def products(page=1):
    products_per_page = 12 
    start_index = (page - 1) * products_per_page

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # CORRECCIÓN AQUÍ: Agregamos un EXISTS para saber si tiene variaciones en la otra tabla
    cursor.execute('''
        SELECT p.*, 
               EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
        FROM products p
        LIMIT %s OFFSET %s
    ''', (products_per_page, start_index))
    products = cursor.fetchall()

    cursor.execute('SELECT COUNT(*) AS count FROM products')
    total_products = cursor.fetchone()["count"]
    total_pages = (total_products // products_per_page) + (total_products % products_per_page > 0)

    conn.close()

    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))

    return render_template('details.html', products=products, total_pages=total_pages, current_page=page, user_logged_in=user_logged_in, user_name=user_name)

@app.route("/product/<int:product_id>")
def product(product_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute('SELECT * FROM products WHERE id = %s', (product_id,))
    product = cursor.fetchone()

    if not product:
        conn.close()
        return render_template("apology.html")

    # Obtener especificaciones
    cursor.execute("SELECT * FROM product_specifications WHERE product_id = %s", (product_id,))
    product_specifications = cursor.fetchall()

    # Obtener imágenes extra
    cursor.execute("SELECT image_url FROM product_images WHERE product_id = %s", (product_id,))
    extra_images = cursor.fetchall()
    
    # CORREGIDO: Aquí es donde agregamos "type" al SELECT
    cursor.execute("SELECT type, name, price FROM product_variations WHERE product_id = %s", (product_id,))
    variations = cursor.fetchall()

    # Productos relacionados
    cursor.execute('''
        SELECT * FROM products 
        WHERE id_category = %s AND id != %s 
        ORDER BY RANDOM() LIMIT 6
    ''', (product["id_category"], product_id))
    related_products = cursor.fetchall()

    conn.close()

    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))

    # Pasamos las variaciones al HTML
    return render_template('products.html', 
                           product=product, 
                           product_specifications=product_specifications,
                           extra_images=extra_images,
                           variations=variations,
                           related_products=related_products, 
                           user_logged_in=user_logged_in, 
                           user_name=user_name)

@app.route("/products_by_category/<int:category_id>", defaults={'page': 1})
@app.route("/products_by_category/<int:category_id>/<int:page>")
def products_by_category(category_id, page=1):
    products_per_page = 12 
    start_index = (page - 1) * products_per_page

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute('''
            SELECT p.*, 
                EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
            FROM products p 
            WHERE id_category = %s 
            LIMIT %s OFFSET %s
        ''', (category_id, products_per_page, start_index))
    products = cursor.fetchall()

    cursor.execute('SELECT COUNT(*) AS count FROM products WHERE id_category = %s', (category_id,))
    total_products = cursor.fetchone()["count"]
    total_pages = (total_products // products_per_page) + (total_products % products_per_page > 0)

    conn.close()

    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))

    return render_template('products_by_category.html', products=products, total_pages=total_pages, current_page=page, category_id=category_id, user_logged_in=user_logged_in, user_name=user_name)

@app.route('/search', defaults={'page': 1})
@app.route('/search/<int:page>')
def search(page=1):
    texto_busqueda = request.args.get('texto_busqueda', '').strip()
    products_per_page = 12 
    start_index = (page - 1) * products_per_page

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    resultados = []
    otros_productos = []
    total_products = 0

    if texto_busqueda:
        # 1. Buscar coincidencias exactas (¡AÑADIMOS LA CONSULTA DE VARIACIONES AQUÍ!)
        cursor.execute("""
            SELECT p.*, EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
            FROM products p 
            WHERE p.name ILIKE %s OR p.description ILIKE %s 
            LIMIT %s OFFSET %s
        """, (f'%{texto_busqueda}%', f'%{texto_busqueda}%', products_per_page, start_index))
        resultados = cursor.fetchall()

        # 2. Contar SOLO las coincidencias exactas
        cursor.execute("""
            SELECT COUNT(*) AS count FROM products p
            WHERE p.name ILIKE %s OR p.description ILIKE %s
        """, (f'%{texto_busqueda}%', f'%{texto_busqueda}%'))
        total_products = cursor.fetchone()["count"]
    else:
        # Si entran sin texto, todo el catálogo (¡CON VARIACIONES!)
        cursor.execute("""
            SELECT p.*, EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
            FROM products p LIMIT %s OFFSET %s
        """, (products_per_page, start_index))
        resultados = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS count FROM products")
        total_products = cursor.fetchone()["count"]

    # 3. Lógica de relleno: Si hay menos de 12 ítems, rellenamos
    if len(resultados) < products_per_page:
        faltantes = products_per_page - len(resultados)
        
        if resultados:
            ids_excluir = tuple([p['id'] for p in resultados])
            cursor.execute("""
                SELECT p.*, EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
                FROM products p 
                WHERE p.id NOT IN %s 
                ORDER BY RANDOM() LIMIT %s
            """, (ids_excluir, faltantes))
        else:
            cursor.execute("""
                SELECT p.*, EXISTS(SELECT 1 FROM product_variations pv WHERE pv.product_id = p.id) AS variaciones
                FROM products p 
                ORDER BY RANDOM() LIMIT %s
            """, (faltantes,))
            
        otros_productos = cursor.fetchall()

    # 4. Cálculo final de páginas
    total_pages = (total_products // products_per_page) + (total_products % products_per_page > 0)
    if total_pages == 0:
        total_pages = 1

    conn.close()

    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))

    return render_template('search.html', 
                           resultados=resultados,
                           otros_productos=otros_productos,
                           total_pages=total_pages, 
                           current_page=page, 
                           user_logged_in=user_logged_in, 
                           user_name=user_name,
                           texto_busqueda=texto_busqueda)




@app.route('/confirm', methods=["GET", "POST"])
def confirm():
    return render_template('confirm.html')



def normalizar_para_busqueda(texto):
    """Normaliza el texto para búsquedas insensibles a tildes, mayúsculas y caracteres especiales"""
    if not texto: 
        return ""
    texto = str(texto).lower()
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    texto = texto.replace('*', 'x')
    texto = re.sub(r'[^a-z0-9]', '', texto)
    return texto

@app.route('/api/productos/buscar_instantaneo')
def buscar_productos_instantaneo():
    """Búsqueda en tiempo real flexible para el menú desplegable del cliente"""
    try:
        query_raw = request.args.get('q', '').strip()
        
        # Si hay menos de 2 caracteres, no buscamos para ahorrar recursos
        if len(query_raw) < 2:
            return jsonify({'success': True, 'productos': []})

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Traemos todos los productos (Si tu base crece mucho, esto se puede optimizar directo en SQL)
        cursor.execute("SELECT * FROM products")
        productos_db = cursor.fetchall()
        conn.close()

        resultados = []
        terminos_busqueda = query_raw.split()
        terminos_normalizados = [normalizar_para_busqueda(t) for t in terminos_busqueda]

        for p in productos_db:
            # Creamos la 'huella' del producto sumando nombre y descripción
            info_producto = f"{p['name']} {p.get('description', '')}"
            info_producto_norm = normalizar_para_busqueda(info_producto)

            # Verificamos si todos los términos buscados están en la huella del producto
            coincide = True
            for termino in terminos_normalizados:
                if termino not in info_producto_norm:
                    coincide = False
                    break

            if coincide:
                resultados.append({
                    'id': p['id'],
                    'nombre': p['name'],
                    'precio': float(p['price']) if p['price'] else 0.0, 
                    'imagen': p['url']
                })

        # Ordenar alfabéticamente y limitar a 10 resultados para no saturar la vista
        resultados.sort(key=lambda x: x['nombre'])
        resultados = resultados[:10]

        return jsonify({
            'success': True,
            'productos': resultados
        })

    except Exception as e:
        print(f"Error en búsqueda instantánea: {e}")
        return jsonify({'success': False, 'productos': [], 'message': str(e)})




if __name__ == '__main__':
    # host='0.0.0.0' expone la app a tu red local
    app.run(host='0.0.0.0', port=5000, debug=True)