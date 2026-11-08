from flask import Flask, request, jsonify, send_file
from conexion import conectar
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import os
import ipaddress

app = Flask(__name__)

# =====================================================
# CONFIGURACIÓN
# =====================================================

RUTA_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "styles1.html"
)

HORAS_GRACIA = 24


def respuesta_error(mensaje, codigo=400):
    return jsonify({"error": mensaje}), codigo


def fecha_texto(valor):
    return valor.isoformat() if valor else None


def obtener_hora_simulada(cursor=None):
    cerrar = False
    conexion = None

    if cursor is None:
        conexion = conectar()

        if conexion is None:
            return datetime.now()

        cursor = conexion.cursor(dictionary=True)
        cerrar = True

    try:
        cursor.execute("""
            SELECT valor
            FROM configuracion_sistema
            WHERE clave = 'horas_simulacion'
        """)

        fila = cursor.fetchone()
        horas = int(fila["valor"]) if fila else 0

        return datetime.now() + timedelta(hours=horas)

    finally:
        if cerrar:
            cursor.close()
            conexion.close()


def registrar_historial(cursor, id_contrato, accion, motivo):
    cursor.execute("""
        INSERT INTO historial_red
        (id_contrato, accion, motivo, fecha)
        VALUES (%s, %s, %s, %s)
    """, (
        id_contrato,
        accion,
        motivo,
        obtener_hora_simulada(cursor)
    ))


def evaluar_contratos(cursor):
    """
    Comprueba las facturas pendientes.
    Si pasan 24 horas después del vencimiento, suspende el contrato.
    Si todas las facturas están pagadas, reactiva el contrato.
    No modifica contratos eliminados.
    """

    ahora = obtener_hora_simulada(cursor)

    cursor.execute("""
        SELECT
            c.id_contrato,
            c.estado,
            c.ip_cliente
        FROM contrato c
        WHERE LOWER(c.estado) != 'eliminado'
        ORDER BY c.id_contrato
    """)

    contratos = cursor.fetchall()

    for contrato in contratos:
        id_contrato = contrato["id_contrato"]

        cursor.execute("""
            SELECT
                f.id_factura,
                f.total,
                f.fecha_vencimiento_exacta,
                COALESCE(SUM(pg.valor), 0) AS pagado
            FROM factura f
            LEFT JOIN pago pg
                ON pg.id_factura = f.id_factura
            WHERE f.id_contrato = %s
            GROUP BY
                f.id_factura,
                f.total,
                f.fecha_vencimiento_exacta
        """, (id_contrato,))

        facturas = cursor.fetchall()
        tiene_mora = False

        for factura in facturas:
            total = Decimal(str(factura["total"]))
            pagado = Decimal(str(factura["pagado"]))
            saldo = total - pagado

            vencimiento = factura["fecha_vencimiento_exacta"]

            if saldo > 0 and vencimiento:
                limite = vencimiento + timedelta(hours=HORAS_GRACIA)

                if ahora >= limite:
                    tiene_mora = True
                    break

        estado_actual = (contrato["estado"] or "").lower()

        if tiene_mora and estado_actual != "suspendido":
            cursor.execute("""
                UPDATE contrato
                SET estado = 'suspendido'
                WHERE id_contrato = %s
            """, (id_contrato,))

            registrar_historial(
                cursor,
                id_contrato,
                "CORTAR",
                "Suspensión automática por factura vencida y período de gracia agotado"
            )

        elif not tiene_mora and estado_actual == "suspendido":
            cursor.execute("""
                UPDATE contrato
                SET estado = 'activo'
                WHERE id_contrato = %s
            """, (id_contrato,))

            registrar_historial(
                cursor,
                id_contrato,
                "ACTIVAR",
                "Reactivación automática por regularización de pagos"
            )


def preparar_base_datos():
    conexion = conectar()

    if conexion is None:
        print("No se pudo conectar con MySQL.")
        return

    cursor = conexion.cursor(dictionary=True)

    try:
        planes = [
            ("Básico 5 Mbps", "5 Mbps", 15.00),
            ("Plus 10 Mbps", "10 Mbps", 20.00),
            ("Pro 20 Mbps", "20 Mbps", 30.00)
        ]

        for nombre, velocidad, precio in planes:
            cursor.execute("""
                SELECT id_plan
                FROM plan
                WHERE nombre = %s
            """, (nombre,))

            if cursor.fetchone() is None:
                cursor.execute("""
                    INSERT INTO plan (nombre, velocidad, precio)
                    VALUES (%s, %s, %s)
                """, (nombre, velocidad, precio))

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS configuracion_sistema (
                clave VARCHAR(100) PRIMARY KEY,
                valor VARCHAR(255) NOT NULL
            )
        """)

        cursor.execute("""
            INSERT IGNORE INTO configuracion_sistema (clave, valor)
            VALUES ('horas_simulacion', '0')
        """)

        cursor.execute("""
            SELECT COUNT(*) AS cantidad
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'factura'
              AND COLUMN_NAME = 'fecha_vencimiento_exacta'
        """)

        existe = cursor.fetchone()["cantidad"]

        if not existe:
            cursor.execute("""
                ALTER TABLE factura
                ADD COLUMN fecha_vencimiento_exacta DATETIME NULL
            """)

        cursor.execute("""
            UPDATE factura
            SET fecha_vencimiento_exacta =
                TIMESTAMP(fecha_vencimiento, '23:59:59')
            WHERE fecha_vencimiento_exacta IS NULL
        """)

        conexion.commit()
        print("Base de datos preparada correctamente.")

    except Exception as e:
        conexion.rollback()
        print("Error preparando la base de datos:", e)

    finally:
        cursor.close()
        conexion.close()


# =====================================================
# PÁGINA PRINCIPAL
# =====================================================

@app.route("/")
def inicio():
    if not os.path.exists(RUTA_HTML):
        return "No se encontró styles1.html en la carpeta del proyecto.", 404

    return send_file(RUTA_HTML)
# =====================================================
# CLIENTES
# =====================================================

@app.route("/api/clientes", methods=["GET"])
def obtener_clientes():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id_cliente,
                nombre,
                cedula,
                telefono,
                correo,
                direccion,
                estado
            FROM cliente
            WHERE LOWER(COALESCE(estado, '')) != 'eliminado'
            ORDER BY id_cliente DESC
        """)

        return jsonify(cursor.fetchall())

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# Obtener clientes eliminados
@app.route("/api/clientes/eliminados", methods=["GET"])
def obtener_clientes_eliminados():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id_cliente,
                nombre,
                cedula,
                telefono,
                correo,
                direccion,
                estado
            FROM cliente
            WHERE LOWER(COALESCE(estado, '')) = 'eliminado'
            ORDER BY id_cliente DESC
        """)

        return jsonify(cursor.fetchall())

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# Registrar cliente
@app.route("/api/clientes", methods=["POST"])
def registrar_cliente():
    datos = request.get_json(silent=True) or {}

    nombre = str(datos.get("nombre") or "").strip()
    cedula = str(datos.get("cedula") or "").strip()
    telefono = str(datos.get("telefono") or "").strip()
    correo = str(datos.get("correo") or "").strip()
    direccion = str(datos.get("direccion") or "").strip()

    if not nombre or not cedula:
        return respuesta_error("El nombre y la cédula son obligatorios.")

    if len(cedula) != 10 or not cedula.isdigit():
        return respuesta_error("La cédula debe contener 10 dígitos.")

    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            INSERT INTO cliente
            (nombre, cedula, telefono, correo, direccion, estado)
            VALUES (%s, %s, %s, %s, %s, 'Activo')
        """, (nombre, cedula, telefono, correo, direccion))

        nuevo_id = cursor.lastrowid
        conexion.commit()

        return jsonify({
            "mensaje": "Cliente registrado correctamente.",
            "id_cliente": nuevo_id
        }), 201

    except Exception as e:
        conexion.rollback()

        if "Duplicate entry" in str(e):
            return respuesta_error("Ya existe un cliente con esa cédula.")

        return respuesta_error(str(e))

    finally:
        cursor.close()
        conexion.close()


# Eliminar cliente sin borrar sus registros
@app.route("/api/clientes/<int:id_cliente>", methods=["DELETE"])
def eliminar_cliente(id_cliente):
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id_cliente, estado
            FROM cliente
            WHERE id_cliente = %s
        """, (id_cliente,))

        cliente = cursor.fetchone()

        if cliente is None:
            return respuesta_error("El cliente no existe.", 404)

        if (cliente["estado"] or "").lower() == "eliminado":
            return respuesta_error("El cliente ya fue eliminado.")

        # Marcar sus contratos como eliminados
        cursor.execute("""
            UPDATE contrato
            SET estado = 'eliminado'
            WHERE id_cliente = %s
        """, (id_cliente,))

        # Marcar el cliente como eliminado
        cursor.execute("""
            UPDATE cliente
            SET estado = 'eliminado'
            WHERE id_cliente = %s
        """, (id_cliente,))

        conexion.commit()

        return jsonify({
            "mensaje": "Cliente eliminado correctamente. Sus contratos, facturas, pagos e historial se conservaron."
        })

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# Restaurar cliente
@app.route("/api/clientes/<int:id_cliente>/restaurar", methods=["POST"])
def restaurar_cliente(id_cliente):
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id_cliente, estado
            FROM cliente
            WHERE id_cliente = %s
        """, (id_cliente,))

        cliente = cursor.fetchone()

        if cliente is None:
            return respuesta_error("El cliente no existe.", 404)

        if (cliente["estado"] or "").lower() != "eliminado":
            return respuesta_error("Este cliente no está eliminado.")

        cursor.execute("""
            UPDATE cliente
            SET estado = 'Activo'
            WHERE id_cliente = %s
        """, (id_cliente,))

        conexion.commit()

        return jsonify({
            "mensaje": "Cliente restaurado correctamente. Ahora puedes volver a registrar un contrato."
        })

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# =====================================================
# PLANES
# =====================================================

@app.route("/api/planes", methods=["GET"])
def obtener_planes():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id_plan, nombre, velocidad, precio
            FROM plan
            WHERE LOWER(estado) = 'activo'
            ORDER BY id_plan
        """)

        resultados = cursor.fetchall()

        for fila in resultados:
            fila["precio"] = float(fila["precio"])

        return jsonify(resultados)

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()

# =====================================================
# CONTRATOS
# =====================================================

@app.route("/api/contratos/<int:id_contrato>", methods=["DELETE"])
def eliminar_contrato(id_contrato):
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id_contrato, estado
            FROM contrato
            WHERE id_contrato = %s
        """, (id_contrato,))

        contrato = cursor.fetchone()

        if contrato is None:
            return respuesta_error("El contrato no existe.", 404)

        if (contrato["estado"] or "").lower() == "eliminado":
            return respuesta_error("El contrato ya fue eliminado.")

        cursor.execute("""
            SELECT
                f.id_factura,
                f.total,
                COALESCE(SUM(p.valor), 0) AS pagado
            FROM factura f
            LEFT JOIN pago p
                ON f.id_factura = p.id_factura
            WHERE f.id_contrato = %s
            GROUP BY f.id_factura, f.total
        """, (id_contrato,))

        facturas = cursor.fetchall()

        for factura in facturas:
            total = Decimal(str(factura["total"]))
            pagado = Decimal(str(factura["pagado"]))

            if total - pagado > 0:
                return respuesta_error(
                    "No se puede eliminar el contrato porque tiene facturas pendientes."
                )

        cursor.execute("""
            UPDATE contrato
            SET estado = 'eliminado'
            WHERE id_contrato = %s
        """, (id_contrato,))

        conexion.commit()

        return jsonify({
            "mensaje": "Contrato eliminado correctamente. Sus facturas, pagos e historial se conservaron."
        })

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


@app.route("/api/contratos", methods=["GET"])
def obtener_contratos():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                c.id_contrato,
                c.id_cliente,
                c.id_plan,
                c.ip_cliente,
                c.fecha_inicio,
                c.estado,
                cl.nombre AS nombre_cliente,
                p.nombre AS nombre_plan,
                p.precio
            FROM contrato c
            INNER JOIN cliente cl
                ON c.id_cliente = cl.id_cliente
            INNER JOIN plan p
                ON c.id_plan = p.id_plan
            WHERE LOWER(c.estado) != 'eliminado'
              AND LOWER(COALESCE(cl.estado, '')) != 'eliminado'
            ORDER BY c.id_contrato DESC
        """)

        resultados = cursor.fetchall()

        for fila in resultados:
            fila["fecha_inicio"] = fecha_texto(fila["fecha_inicio"])
            fila["precio"] = float(fila["precio"])

        return jsonify(resultados)

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


@app.route("/api/contratos", methods=["POST"])
def registrar_contrato():
    datos = request.get_json(silent=True) or {}

    id_cliente = datos.get("id_cliente")
    id_plan = datos.get("id_plan")
    ip = str(datos.get("ip_cliente") or "").strip()

    if not id_cliente or not id_plan or not ip:
        return respuesta_error("Completa todos los datos del contrato.")

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return respuesta_error("La dirección IP no es válida.")

    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor()

    try:
        # Comprobar que el cliente exista y esté activo
        cursor.execute("""
            SELECT estado
            FROM cliente
            WHERE id_cliente = %s
        """, (id_cliente,))

        cliente = cursor.fetchone()

        if cliente is None:
            return respuesta_error("El cliente no existe.", 404)

        if (cliente[0] or "").lower() == "eliminado":
            return respuesta_error(
                "Este cliente está eliminado. Primero debes restaurarlo."
            )

        # Comprobar que el plan exista y esté activo
        cursor.execute("""
            SELECT id_plan
            FROM plan
            WHERE id_plan = %s
              AND LOWER(estado) = 'activo'
        """, (id_plan,))

        if cursor.fetchone() is None:
            return respuesta_error("El plan no existe o no está activo.")

        cursor.execute("""
            INSERT INTO contrato
            (id_cliente, id_plan, ip_cliente, fecha_inicio, estado)
            VALUES (%s, %s, %s, %s, 'activo')
        """, (
            id_cliente,
            id_plan,
            ip,
            obtener_hora_simulada()
        ))

        nuevo_id = cursor.lastrowid
        conexion.commit()

        return jsonify({
            "mensaje": "Contrato registrado correctamente.",
            "id_contrato": nuevo_id
        }), 201

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()

# =====================================================
# FACTURAS
# =====================================================

@app.route("/api/facturas", methods=["GET"])
def obtener_facturas():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                f.id_factura,
                f.id_contrato,
                f.fecha_emision,
                f.fecha_vencimiento,
                f.fecha_vencimiento_exacta,
                f.total,
                f.estado,
                f.periodo,
                cl.nombre AS nombre_cliente,
                p.nombre AS nombre_plan,
                COALESCE(SUM(pg.valor), 0) AS pagado
            FROM factura f
            INNER JOIN contrato c
                ON f.id_contrato = c.id_contrato
            INNER JOIN cliente cl
                ON c.id_cliente = cl.id_cliente
            INNER JOIN plan p
                ON c.id_plan = p.id_plan
            LEFT JOIN pago pg
                ON f.id_factura = pg.id_factura
            GROUP BY
                f.id_factura,
                f.id_contrato,
                f.fecha_emision,
                f.fecha_vencimiento,
                f.fecha_vencimiento_exacta,
                f.total,
                f.estado,
                f.periodo,
                cl.nombre,
                p.nombre
            ORDER BY f.id_factura DESC
        """)

        resultados = cursor.fetchall()

        for fila in resultados:
            fila["fecha_emision"] = fecha_texto(fila["fecha_emision"])
            fila["fecha_vencimiento"] = fecha_texto(fila["fecha_vencimiento"])
            fila["fecha_vencimiento_exacta"] = fecha_texto(
                fila["fecha_vencimiento_exacta"]
            )
            fila["total"] = float(fila["total"])
            fila["pagado"] = float(fila["pagado"])

        return jsonify(resultados)

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


@app.route("/api/facturas", methods=["POST"])
def registrar_factura():
    datos = request.get_json(silent=True) or {}

    id_contrato = datos.get("id_contrato")

    try:
        valor = Decimal(str(datos.get("valor")))

        if not valor.is_finite() or valor <= 0:
            raise InvalidOperation()

    except (InvalidOperation, ValueError):
        return respuesta_error("El valor de la factura no es válido.")

    if not id_contrato:
        return respuesta_error("Selecciona un contrato.")

    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                p.precio,
                c.estado
            FROM contrato c
            INNER JOIN plan p
                ON c.id_plan = p.id_plan
            WHERE c.id_contrato = %s
        """, (id_contrato,))

        contrato = cursor.fetchone()

        if contrato is None:
            return respuesta_error("El contrato no existe.", 404)

        if (contrato["estado"] or "").lower() == "eliminado":
            return respuesta_error(
                "No se pueden generar facturas para un contrato eliminado."
            )

        precio = Decimal(str(contrato["precio"]))

        if valor != precio:
            return respuesta_error(
                "El valor debe coincidir con el precio del plan."
            )

        ahora = obtener_hora_simulada(cursor)
        vencimiento = ahora + timedelta(hours=48)
        periodo = ahora.strftime("%Y-%m")

        cursor.execute("""
            SELECT id_factura
            FROM factura
            WHERE id_contrato = %s
              AND periodo = %s
        """, (id_contrato, periodo))

        if cursor.fetchone():
            return respuesta_error(
                "Este contrato ya tiene una factura para este período."
            )

        cursor.execute("""
            INSERT INTO factura
            (
                id_contrato,
                fecha_emision,
                fecha_vencimiento,
                fecha_vencimiento_exacta,
                total,
                estado,
                periodo
            )
            VALUES (%s, %s, %s, %s, %s, 'pendiente', %s)
        """, (
            id_contrato,
            ahora.date(),
            vencimiento.date(),
            vencimiento,
            valor,
            periodo
        ))

        nuevo_id = cursor.lastrowid

        evaluar_contratos(cursor)
        conexion.commit()

        return jsonify({
            "mensaje": "Factura creada correctamente.",
            "id_factura": nuevo_id
        }), 201

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e))

    finally:
        cursor.close()
        conexion.close()


# =====================================================
# PAGOS
# =====================================================

@app.route("/api/pagos", methods=["GET"])
def obtener_pagos():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id_pago,
                id_factura,
                fecha_pago,
                valor,
                referencia
            FROM pago
            ORDER BY id_pago DESC
        """)

        resultados = cursor.fetchall()

        for fila in resultados:
            fila["fecha_pago"] = fecha_texto(fila["fecha_pago"])
            fila["valor"] = float(fila["valor"])

        return jsonify(resultados)

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


@app.route("/api/pagos", methods=["POST"])
def registrar_pago():
    datos = request.get_json(silent=True) or {}

    id_factura = datos.get("id_factura")
    referencia = str(datos.get("referencia") or "").strip()

    try:
        valor = Decimal(str(datos.get("valor")))

        if not valor.is_finite() or valor <= 0:
            raise InvalidOperation()

    except (InvalidOperation, ValueError):
        return respuesta_error("El valor del pago no es válido.")

    if not id_factura or not referencia:
        return respuesta_error(
            "Selecciona una factura e ingresa la referencia."
        )

    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT total
            FROM factura
            WHERE id_factura = %s
            FOR UPDATE
        """, (id_factura,))

        factura = cursor.fetchone()

        if factura is None:
            return respuesta_error("La factura no existe.", 404)

        cursor.execute("""
            SELECT COALESCE(SUM(valor), 0) AS pagado
            FROM pago
            WHERE id_factura = %s
        """, (id_factura,))

        pagado = Decimal(str(cursor.fetchone()["pagado"]))
        total = Decimal(str(factura["total"]))
        saldo = total - pagado

        if saldo <= 0:
            return respuesta_error("Esta factura ya está pagada.")

        if valor > saldo:
            return respuesta_error("El pago supera el saldo pendiente.")

        cursor.execute("""
            INSERT INTO pago
            (id_factura, fecha_pago, valor, referencia)
            VALUES (%s, %s, %s, %s)
        """, (
            id_factura,
            obtener_hora_simulada(cursor),
            valor,
            referencia
        ))

        nuevo_saldo = saldo - valor
        estado = "pagada" if nuevo_saldo == 0 else "pendiente"

        cursor.execute("""
            UPDATE factura
            SET estado = %s
            WHERE id_factura = %s
        """, (estado, id_factura))

        evaluar_contratos(cursor)
        conexion.commit()

        return jsonify({
            "mensaje": "Pago registrado correctamente.",
            "saldo": float(nuevo_saldo)
        }), 201

    except Exception as e:
        conexion.rollback()

        if "Duplicate entry" in str(e):
            return respuesta_error(
                "Esta referencia de pago ya fue registrada."
            )

        return respuesta_error(str(e))

    finally:
        cursor.close()
        conexion.close()

# =====================================================
# HISTORIAL DE RED
# =====================================================

@app.route("/api/historial-red", methods=["GET"])
def obtener_historial():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                h.id_historial,
                h.id_contrato,
                h.accion,
                h.motivo,
                h.fecha,
                cl.nombre AS nombre_cliente,
                c.ip_cliente
            FROM historial_red h
            INNER JOIN contrato c
                ON h.id_contrato = c.id_contrato
            INNER JOIN cliente cl
                ON c.id_cliente = cl.id_cliente
            ORDER BY h.fecha DESC, h.id_historial DESC
        """)

        resultados = cursor.fetchall()

        for fila in resultados:
            fila["fecha"] = fecha_texto(fila["fecha"])

        return jsonify(resultados)

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# =====================================================
# RELOJ DE SIMULACIÓN
# =====================================================

@app.route("/api/simulacion", methods=["GET"])
def obtener_simulacion():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT valor
            FROM configuracion_sistema
            WHERE clave = 'horas_simulacion'
        """)

        fila = cursor.fetchone()
        horas = int(fila["valor"]) if fila else 0

        return jsonify({
            "horas": horas,
            "fecha": (
                datetime.now() + timedelta(hours=horas)
            ).isoformat()
        })

    except Exception as e:
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


@app.route("/api/simulacion", methods=["POST"])
def modificar_simulacion():
    datos = request.get_json(silent=True) or {}
    accion = datos.get("accion")

    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT valor
            FROM configuracion_sistema
            WHERE clave = 'horas_simulacion'
            FOR UPDATE
        """)

        fila = cursor.fetchone()
        horas = int(fila["valor"]) if fila else 0

        if accion == "avanzar":
            incremento = datos.get("horas")

            if incremento not in (1, 24, 72):
                return respuesta_error(
                    "El avance de tiempo no es válido."
                )

            horas += int(incremento)

        elif accion == "reiniciar":
            horas = 0

        else:
            return respuesta_error(
                "Acción de simulación no válida."
            )

        cursor.execute("""
            UPDATE configuracion_sistema
            SET valor = %s
            WHERE clave = 'horas_simulacion'
        """, (str(horas),))

        evaluar_contratos(cursor)
        conexion.commit()

        return jsonify({
            "mensaje": "Reloj actualizado correctamente.",
            "horas": horas,
            "fecha": (
                datetime.now() + timedelta(hours=horas)
            ).isoformat()
        })

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# =====================================================
# EVALUAR SISTEMA
# =====================================================

@app.route("/api/evaluar", methods=["POST"])
def evaluar_sistema():
    conexion = conectar()

    if conexion is None:
        return respuesta_error("No hay conexión con MySQL.", 500)

    cursor = conexion.cursor(dictionary=True)

    try:
        evaluar_contratos(cursor)
        conexion.commit()

        return jsonify({
            "mensaje": "Estados comprobados correctamente."
        })

    except Exception as e:
        conexion.rollback()
        return respuesta_error(str(e), 500)

    finally:
        cursor.close()
        conexion.close()


# =====================================================
# INICIAR SISTEMA
# =====================================================

if __name__ == "__main__":
    preparar_base_datos()
    app.run(debug=True)