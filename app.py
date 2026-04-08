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
    return render_template("index.html", user_logged_in=user_logged_in, user_name=user_name)

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

        # --- LÓGICA MULTI-IMAGEN ---
        # Obtenemos la lista de TODOS los archivos subidos
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
                    buffer_webp.seek(0)
                    
                    file_name = f"{uuid.uuid4().hex}.webp"
                    
                    supabase.storage.from_("productos").upload(
                        path=file_name,
                        file=buffer_webp.read(),
                        file_options={"content-type": "image/webp"}
                    )
                    # Guardamos la URL pública en nuestra lista
                    public_url = supabase.storage.from_("productos").get_public_url(file_name)
                    urls_subidas.append(public_url)
                except Exception as e:
                    print(f"Error procesando imagen: {e}")

        # Definir la imagen principal (la primera subida, o el link manual, o vacío)
        main_url = urls_subidas[0] if urls_subidas else (manual_url if manual_url else "")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. Insertar producto con su imagen principal
        cursor.execute("""
            INSERT INTO products (name, price, description, id_category, url) 
            VALUES(%s, %s, %s, %s, %s) RETURNING id
        """, (name, price, description, category, main_url))
        
        product_id = cursor.fetchone()["id"]

        # 2. Insertar el resto de las imágenes en la nueva tabla (si hay más de una)
        if len(urls_subidas) > 1:
            for extra_url in urls_subidas[1:]:
                cursor.execute("""
                    INSERT INTO product_images (product_id, image_url) 
                    VALUES (%s, %s)
                """, (product_id, extra_url))

        # 3. Guardar especificaciones
        for key in request.form:
            if key.startswith("attribute") and request.form[key]:
                attribute_num = key.replace("attribute", "")
                attribute = request.form[key]
                value = request.form.get("value" + attribute_num)
                cursor.execute("""
                    INSERT INTO product_specifications (product_id, attribute, value) 
                    VALUES (%s, %s, %s)
                """, (product_id, attribute, value))

        conn.commit()
        conn.close()

        return render_template("send.html")
    else:
        if session.get("user_id") is None:
            return redirect("/login")
        if session["user_id"] == 1:
            return render_template("send.html")
        return redirect("/")
    


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

    cursor.execute('SELECT * FROM products LIMIT %s OFFSET %s', (products_per_page, start_index))
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

    return render_template('products.html', 
                           product=product, 
                           product_specifications=product_specifications,
                           extra_images=extra_images,
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

    cursor.execute('SELECT * FROM products WHERE id_category = %s LIMIT %s OFFSET %s', 
                   (category_id, products_per_page, start_index))
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
    texto_busqueda = request.args.get('texto_busqueda', '')
    products_per_page = 12 
    start_index = (page - 1) * products_per_page

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute("SELECT * FROM products WHERE name LIKE %s LIMIT %s OFFSET %s", 
                   ('%' + texto_busqueda + '%', products_per_page, start_index))
    resultados = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS count FROM products WHERE name LIKE %s", 
                   ('%' + texto_busqueda + '%',))
    total_products = cursor.fetchone()["count"]
    total_pages = (total_products // products_per_page) + (total_products % products_per_page > 0)

    conn.close()

    user_logged_in = "user_id" in session
    user_name = get_user_name(session.get("user_id"))

    return render_template('search.html', resultados=resultados, total_pages=total_pages, current_page=page, user_logged_in=user_logged_in, user_name=user_name)

@app.route('/confirm', methods=["GET", "POST"])
def confirm():
    return render_template('confirm.html')

if __name__ == '__main__':
    # host='0.0.0.0' expone la app a tu red local
    app.run(host='0.0.0.0', port=5000, debug=True)