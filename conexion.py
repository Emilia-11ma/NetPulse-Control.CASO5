import mysql.connector
from mysql.connector import Error


def conectar():
    try:
        return mysql.connector.connect(
            host="localhost",
            user="Admin",
            password="root",
            database="netpulse_control",
            port=3306
        )
    except Error as error:
        print("Error de conexión MySQL:", error)
        return None