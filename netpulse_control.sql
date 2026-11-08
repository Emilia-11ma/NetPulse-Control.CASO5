
-- ==========================================
-- NETPULSE CONTROL
-- Base de datos independiente
-- ==========================================

CREATE DATABASE IF NOT EXISTS netpulse_control
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE netpulse_control;

-- ==========================================
-- 1. TABLA DE CLIENTES
-- ==========================================

CREATE TABLE IF NOT EXISTS cliente (
    id_cliente INT AUTO_INCREMENT PRIMARY KEY,
    cedula VARCHAR(15) NOT NULL UNIQUE,
    nombre VARCHAR(100) NOT NULL,
    telefono VARCHAR(20),
    correo VARCHAR(100),
    direccion VARCHAR(200),
    estado VARCHAR(20) DEFAULT 'activo'
);

-- ==========================================
-- 2. TABLA DE PLANES
-- ==========================================

CREATE TABLE IF NOT EXISTS plan (
    id_plan INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    velocidad VARCHAR(50),
    precio DECIMAL(10,2) NOT NULL,
    estado VARCHAR(20) DEFAULT 'activo'
);

-- ==========================================
-- 3. TABLA DE CONTRATOS
-- ==========================================

CREATE TABLE IF NOT EXISTS contrato (
    id_contrato INT AUTO_INCREMENT PRIMARY KEY,
    id_cliente INT NOT NULL,
    id_plan INT NOT NULL,
    ip_cliente VARCHAR(45),
    fecha_inicio DATE NOT NULL,
    estado VARCHAR(20) DEFAULT 'activo',

    FOREIGN KEY (id_cliente)
        REFERENCES cliente(id_cliente),

    FOREIGN KEY (id_plan)
        REFERENCES plan(id_plan)
);

-- ==========================================
-- 4. TABLA DE FACTURAS
-- ==========================================

CREATE TABLE IF NOT EXISTS factura (
    id_factura INT AUTO_INCREMENT PRIMARY KEY,
    id_contrato INT NOT NULL,
    fecha_emision DATETIME NOT NULL,
    fecha_vencimiento DATETIME NOT NULL,
    total DECIMAL(10,2) NOT NULL,
    estado VARCHAR(20) DEFAULT 'pendiente',
    periodo VARCHAR(7) NOT NULL,

    UNIQUE (id_contrato, periodo),

    FOREIGN KEY (id_contrato)
        REFERENCES contrato(id_contrato)
);

-- ==========================================
-- 5. TABLA DE PAGOS
-- ==========================================

CREATE TABLE IF NOT EXISTS pago (
    id_pago INT AUTO_INCREMENT PRIMARY KEY,
    id_factura INT NOT NULL,
    fecha_pago DATETIME NOT NULL,
    valor DECIMAL(10,2) NOT NULL,
    referencia VARCHAR(100) NOT NULL UNIQUE,

    FOREIGN KEY (id_factura)
        REFERENCES factura(id_factura)
);

-- ==========================================
-- 6. HISTORIAL DE RED
-- ==========================================

CREATE TABLE IF NOT EXISTS historial_red (
    id_historial INT AUTO_INCREMENT PRIMARY KEY,
    id_contrato INT NOT NULL,
    accion VARCHAR(30) NOT NULL,
    motivo VARCHAR(200),
    fecha DATETIME NOT NULL,

    FOREIGN KEY (id_contrato)
        REFERENCES contrato(id_contrato)
);

-- ==========================================
-- 7. VERIFICAR BASE DE DATOS
-- ==========================================

USE netpulse_control;

SELECT DATABASE();

SHOW TABLES;

DESCRIBE cliente;
DESCRIBE plan;
DESCRIBE contrato;
DESCRIBE factura;
DESCRIBE pago;
DESCRIBE historial_red;

-- ==========================================
-- 8. CONSULTAR REGISTROS
-- ==========================================

SELECT * FROM cliente;
SELECT * FROM plan;
SELECT * FROM contrato;
SELECT * FROM factura;
SELECT * FROM pago;
SELECT * FROM historial_red;
