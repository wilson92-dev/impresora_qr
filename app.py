import os
import uuid
import time
import queue
import subprocess
import threading
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "clave_secreta_impresiones"

# --- CONFIGURACIÓN BASE DE DATOS ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///impresiones.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- CONFIGURACIÓN DE RUTAS Y CUPS ---
BASE_DIR = os.path.dirname(__file__)
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

IMPRESORA_NOMBRE = "wilsonimpresora"
TAMANO_PAPEL = "Letter"
PRECIO_HOJA_BN = 1
PRECIO_HOJA_COLOR = 2
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

# --- MODELO DE BASE DE DATOS ---
class Pedido(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    original_name = db.Column(db.String(200), nullable=False)
    saved_name = db.Column(db.String(200), nullable=False)
    paginas = db.Column(db.Integer, nullable=False)
    copias = db.Column(db.Integer, nullable=False)
    modo = db.Column(db.String(10), nullable=False)  # 'bn' o 'color'
    total = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(50), nullable=False, default='pendiente_pago')
    metodo_pago = db.Column(db.String(20), nullable=True)

with app.app_context():
    db.create_all()

# --- SISTEMA DE COLA Y BLOQUEO PARA IMPRESORA ---
impresora_lock = threading.Lock()
cola_impresion = queue.Queue()

# --- BLOQUEO ESTRICTO DE CACHÉ (Evita que Android guarde formularios en caché) ---
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# --- FUNCIONES AUXILIARES ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def contar_paginas(filepath):
    ext = filepath.rsplit('.', 1)[1].lower()
    if ext == 'pdf':
        try:
            import PyPDF2
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                return len(reader.pages)
        except Exception as e:
            print(f"Error leyendo PDF: {e}")
            return 1
    return 1  # imágenes cuentan como 1 página

# --- WORKER DE IMPRESIÓN (En segundo plano) ---
def worker_impresion():
    while True:
        try:
            pedido_id = cola_impresion.get()
            with app.app_context():
                pedido = Pedido.query.get(pedido_id)
                if not pedido or pedido.estado == 'completado':
                    cola_impresion.task_done()
                    continue

                filepath = os.path.join(app.config['UPLOAD_FOLDER'], pedido.saved_name)
                if not os.path.exists(filepath):
                    print(f"Archivo no encontrado para pedido {pedido.id}: {filepath}")
                    pedido.estado = 'error_archivo'
                    db.session.commit()
                    cola_impresion.task_done()
                    continue

                cmd = [
                    "lp", "-d", IMPRESORA_NOMBRE,
                    "-n", str(pedido.copias),
                    "-o", "fitplot",
                    "-o", f"media={TAMANO_PAPEL}"
                ]

                if pedido.modo == "bn":
                    cmd.extend(["-o", "ColorModel=Gray"])

                cmd.append(filepath)

                try:
                    with impresora_lock:
                        print(f"[{time.strftime('%X')}] Imprimiendo pedido {pedido.id}: {' '.join(cmd)}")
                        subprocess.run(cmd, check=True)
                        time.sleep(1)
                    pedido.estado = 'completado'
                    db.session.commit()
                except subprocess.CalledProcessError as e:
                    print(f"Error al ejecutar lp para pedido {pedido.id}: {e}")
                    pedido.estado = 'error_impresion'
                    db.session.commit()
                except Exception as e:
                    print(f"Error inesperado en impresión pedido {pedido.id}: {e}")
                    pedido.estado = 'error_impresion'
                    db.session.commit()

            cola_impresion.task_done()
        except Exception as e:
            print(f"Error en worker de impresión: {e}")
            try:
                cola_impresion.task_done()
            except Exception:
                pass

threading.Thread(target=worker_impresion, daemon=True).start()

# --- RUTAS PRINCIPALES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'archivo' not in request.files:
        flash("No se encontró archivo en la solicitud.")
        return redirect(url_for('index'))

    file = request.files['archivo']
    if file.filename == '':
        flash("No seleccionaste ningún archivo.")
        return redirect(url_for('index'))

    if file and allowed_file(file.filename):
        modo = request.form.get('modo', 'bn')
        try:
            copias = int(request.form.get('copias', 1))
            if copias < 1:
                copias = 1
        except Exception:
            copias = 1

        filename = secure_filename(file.filename)
        pedido_id = str(uuid.uuid4())
        saved_name = f"{pedido_id}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], saved_name)

        try:
            file.save(filepath)
        except Exception as e:
            flash(f"Error guardando archivo: {e}")
            return redirect(url_for('index'))

        paginas = contar_paginas(filepath)
        precio_unitario = PRECIO_HOJA_BN if modo == 'bn' else PRECIO_HOJA_COLOR
        total = paginas * copias * precio_unitario

        nuevo_pedido = Pedido(
            id=pedido_id,
            original_name=file.filename,
            saved_name=saved_name,
            paginas=paginas,
            copias=copias,
            modo=modo,
            total=total,
            estado='pendiente_pago'
        )
        db.session.add(nuevo_pedido)
        db.session.commit()

        return redirect(url_for('checkout', pedido_id=pedido_id))

    flash("Solo se permiten archivos PDF, PNG o JPG. Convierte tu archivo antes de subirlo.")
    return redirect(url_for('index'))

@app.route('/checkout/<pedido_id>')
def checkout(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    if pedido.estado != 'pendiente_pago':
        return redirect(url_for('index'))
    return render_template('checkout.html', pedido=pedido)

# --- RUTAS DE PAGO ---
def procesar_pago(pedido_id, metodo):
    pedido = Pedido.query.get_or_404(pedido_id)
    if pedido.estado == 'pendiente_pago':
        pedido.estado = 'esperando_aprobacion'
        pedido.metodo_pago = metodo
        db.session.commit()
    return redirect(url_for('espera_caja', pedido_id=pedido.id))

@app.route('/pagar_efectivo/<pedido_id>', methods=['POST'])
def pagar_efectivo(pedido_id):
    return procesar_pago(pedido_id, "efectivo")

@app.route('/pagar_qr/<pedido_id>', methods=['POST'])
def pagar_qr(pedido_id):
    return procesar_pago(pedido_id, "qr")

@app.route('/espera_caja/<pedido_id>')
def espera_caja(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    if pedido.estado in ['aprobado_caja', 'en_cola', 'completado']:
        return redirect(url_for('exito', pedido_id=pedido.id))
    return render_template('espera_caja.html', pedido=pedido)

@app.route('/exito/<pedido_id>')
def exito(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)

    if pedido.estado == 'aprobado_caja':
        cola_impresion.put(pedido.id)
        pedido.estado = 'en_cola'
        db.session.commit()

    return render_template('exito.html', pedido=pedido)

# --- RUTAS DE ADMINISTRACIÓN ---
@app.route('/admin')
def admin():
    pendientes = Pedido.query.filter_by(estado='esperando_aprobacion').all()
    return render_template('admin.html', pendientes=pendientes)

@app.route('/admin/aprobar/<pedido_id>', methods=['POST'])
def aprobar_pedido(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    if pedido.estado == 'esperando_aprobacion':
        pedido.estado = 'aprobado_caja'
        db.session.commit()
        flash(f'Pedido {pedido_id} aprobado e imprimiendo.')
    return redirect(url_for('admin'))

@app.route('/admin/rechazar/<pedido_id>', methods=['POST'])
def rechazar_pedido(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    if pedido.estado == 'esperando_aprobacion':
        pedido.estado = 'rechazado'
        db.session.commit()
        flash(f'Pedido {pedido_id} rechazado.')
    return redirect(url_for('admin'))

@app.route('/admin/reiniciar_cups')
def reiniciar_cups():
    try:
        subprocess.run(["sudo", "systemctl", "restart", "cups"], check=True, timeout=10)
        flash('CUPS y la impresora han sido reiniciados correctamente.')
    except subprocess.CalledProcessError as e:
        flash(f'Error al reiniciar CUPS: {e}')
    except subprocess.TimeoutExpired:
        flash('Tiempo de espera agotado al reiniciar CUPS.')
    except Exception as e:
        flash(f'Aviso al reiniciar CUPS: {e}')
    return redirect(url_for('admin'))

@app.route('/admin/limpiar_cola')
def limpiar_cola():
    try:
        subprocess.run(["cancel", "-a", IMPRESORA_NOMBRE], check=True, timeout=5)
        flash('La cola de impresión ha sido limpiada con éxito.')
    except subprocess.CalledProcessError as e:
        flash(f'Error al limpiar la cola: {e}')
    except subprocess.TimeoutExpired:
        flash('Tiempo de espera agotado al limpiar la cola.')
    except Exception as e:
        flash(f'Error al limpiar la cola: {e}')
    return redirect(url_for('admin'))

# --- EJECUCIÓN ---
if __name__ == '__main__':
    # En producción, usa un servidor WSGI (gunicorn, uWSGI). Aquí para desarrollo.
    app.run(host='0.0.0.0', port=5005, debug=True)

