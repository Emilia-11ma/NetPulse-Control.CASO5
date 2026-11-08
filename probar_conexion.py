from conexion import conectar

conexion = conectar()

if conexion:
    print("NetPulse está conectado a la base de datos.")
    conexion.close()
else:
    print("No se pudo conectar.")