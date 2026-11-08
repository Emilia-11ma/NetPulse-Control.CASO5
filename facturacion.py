"""Ejemplo mínimo: facturación ISP aplicando SOLID, DRY y KISS.

Ejecutar pruebas:  python -m unittest facturacion_isp_ejemplo.py -v
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Protocol
import unittest

GRACIA = timedelta(hours=24)


# ---------- Modelos (solo datos) ----------
class EstadoContrato(Enum):
    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"


@dataclass
class Contrato:
    id: int
    cliente_id: int
    estado: EstadoContrato = EstadoContrato.ACTIVO


@dataclass
class Factura:
    id: int
    contrato_id: int
    valor: Decimal
    vencimiento: datetime
    pagado: Decimal = Decimal("0")

    @property
    def saldo(self) -> Decimal:
        return self.valor - self.pagado

    def esta_en_mora(self, ahora: datetime) -> bool:
        """Mora = saldo pendiente y plazo de gracia vencido (se calcula desde la hora de vencimiento)."""
        return self.saldo > 0 and ahora >= self.vencimiento + GRACIA


# ---------- Validaciones (DRY: una sola vez) ----------
def validar_pago(valor: Decimal) -> None:
    if valor <= 0:
        raise ValueError("El pago debe ser mayor que cero")


# ---------- Repositorios (abstracción: Inversión de dependencias) ----------
class Repositorio(Protocol):
    def facturas_pendientes(self) -> list[Factura]: ...
    def facturas_de(self, contrato_id: int) -> list[Factura]: ...
    def contrato(self, contrato_id: int) -> Contrato: ...
    def transaccion_existe(self, ref: str) -> bool: ...
    def guardar_transaccion(self, ref: str) -> None: ...


@dataclass
class RepositorioMemoria:
    """Implementación en memoria; en producción sería SQL con consultas parametrizadas."""
    contratos: dict[int, Contrato] = field(default_factory=dict)
    facturas: dict[int, Factura] = field(default_factory=dict)
    transacciones: set[str] = field(default_factory=set)

    def facturas_pendientes(self):
        return [f for f in self.facturas.values() if f.saldo > 0]

    def facturas_de(self, contrato_id):
        return [f for f in self.facturas.values() if f.contrato_id == contrato_id]

    def contrato(self, contrato_id):
        return self.contratos[contrato_id]

    def transaccion_existe(self, ref):
        return ref in self.transacciones

    def guardar_transaccion(self, ref):
        self.transacciones.add(ref)


# ---------- Servicios (una responsabilidad cada uno) ----------
class ServicioSuspensiones:
    def __init__(self, repo: Repositorio):
        self.repo = repo

    def suspender_morosos(self, ahora: datetime) -> list[int]:
        """Se ejecuta periódicamente (cron/scheduler); guardar un estado no suspende por sí solo."""
        suspendidos = []
        for f in self.repo.facturas_pendientes():
            c = self.repo.contrato(f.contrato_id)
            if f.esta_en_mora(ahora) and c.estado == EstadoContrato.ACTIVO:
                c.estado = EstadoContrato.SUSPENDIDO
                suspendidos.append(c.id)
        return suspendidos

    def reactivar_si_corresponde(self, contrato_id: int, ahora: datetime) -> bool:
        c = self.repo.contrato(contrato_id)
        hay_mora = any(f.esta_en_mora(ahora) for f in self.repo.facturas_de(contrato_id))
        if c.estado == EstadoContrato.SUSPENDIDO and not hay_mora:
            c.estado = EstadoContrato.ACTIVO
            return True
        return False


class ServicioPagos:
    def __init__(self, repo: Repositorio, suspensiones: ServicioSuspensiones):
        self.repo = repo
        self.suspensiones = suspensiones

    def registrar_pago(self, factura: Factura, valor: Decimal, referencia: str, ahora: datetime) -> None:
        validar_pago(valor)
        if self.repo.transaccion_existe(referencia):
            raise ValueError("Transacción duplicada")
        if valor > factura.saldo:
            raise ValueError("El pago excede el saldo pendiente")
        factura.pagado += valor
        self.repo.guardar_transaccion(referencia)
        self.suspensiones.reactivar_si_corresponde(factura.contrato_id, ahora)


# ---------- Pruebas ----------
class PruebasFacturacion(unittest.TestCase):
    def setUp(self):
        self.venc = datetime(2026, 10, 10, 18, 0)
        self.repo = RepositorioMemoria()
        self.repo.contratos[1] = Contrato(1, cliente_id=1)
        self.factura = Factura(1, 1, Decimal("20"), self.venc)
        self.repo.facturas[1] = self.factura
        self.susp = ServicioSuspensiones(self.repo)
        self.pagos = ServicioPagos(self.repo, self.susp)

    def test_no_suspende_antes_de_24h(self):
        self.assertEqual(self.susp.suspender_morosos(self.venc + timedelta(hours=23, minutes=59)), [])

    def test_suspende_pasadas_24h_desde_el_vencimiento(self):
        self.assertEqual(self.susp.suspender_morosos(self.venc + timedelta(hours=24)), [1])
        self.assertEqual(self.repo.contrato(1).estado, EstadoContrato.SUSPENDIDO)

    def test_pago_completo_reactiva(self):
        ahora = self.venc + timedelta(days=2)
        self.susp.suspender_morosos(ahora)
        self.pagos.registrar_pago(self.factura, Decimal("20"), "TX-1", ahora)
        self.assertEqual(self.repo.contrato(1).estado, EstadoContrato.ACTIVO)

    def test_pago_parcial_no_reactiva(self):
        ahora = self.venc + timedelta(days=2)
        self.susp.suspender_morosos(ahora)
        self.pagos.registrar_pago(self.factura, Decimal("5"), "TX-1", ahora)
        self.assertEqual(self.repo.contrato(1).estado, EstadoContrato.SUSPENDIDO)

    def test_pago_duplicado_rechazado(self):
        self.pagos.registrar_pago(self.factura, Decimal("5"), "TX-1", self.venc)
        with self.assertRaises(ValueError):
            self.pagos.registrar_pago(self.factura, Decimal("5"), "TX-1", self.venc)

    def test_pago_invalido(self):
        with self.assertRaises(ValueError):
            self.pagos.registrar_pago(self.factura, Decimal("0"), "TX-2", self.venc)


if __name__ == "__main__":
    unittest.main()