"""Señales de corte/reactivación del internet (se apoya en facturacion_isp_ejemplo.py).

Ejecutar pruebas:  python -m unittest control_red.py -v
"""
import logging
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from facturacion_isp_ejemplo import (
    Contrato, EstadoContrato, Factura, RepositorioMemoria, ServicioSuspensiones,
)

log = logging.getLogger("control_red")


# ---------- Abstracción: la lógica de negocio no sabe qué router existe ----------
class ControladorRed(Protocol):
    def cortar(self, ip_cliente: str) -> None: ...
    def activar(self, ip_cliente: str) -> None: ...


class ControladorSimulado:
    """Para pruebas y demostración: guarda qué IPs están cortadas."""
    def __init__(self):
        self.cortados: set[str] = set()

    def cortar(self, ip_cliente):
        self.cortados.add(ip_cliente)

    def activar(self, ip_cliente):
        self.cortados.discard(ip_cliente)


class ControladorMikroTik:
    """Corta agregando la IP a la lista 'morosos' del firewall (pip install librouteros).

    En el router debe existir una regla que descarte o redirija el tráfico de esa lista,
    por ejemplo a una página de aviso de pago. No se ha probado contra un equipo real:
    valida los comandos primero en un entorno de prueba.
    """
    LISTA = "morosos"

    def __init__(self, host: str, usuario: str, clave: str):
        from librouteros import connect  # import local: solo se necesita en producción
        self.api = connect(host=host, username=usuario, password=clave)

    def _lista(self):
        return self.api.path("ip", "firewall", "address-list")

    def cortar(self, ip_cliente):
        # Idempotente: si ya está en la lista no se vuelve a agregar
        if not any(r.get("address") == ip_cliente and r.get("list") == self.LISTA
                   for r in self._lista()):
            self._lista().add(list=self.LISTA, address=ip_cliente, comment="corte por mora")

    def activar(self, ip_cliente):
        for r in list(self._lista()):
            if r.get("address") == ip_cliente and r.get("list") == self.LISTA:
                self._lista().remove(r[".id"])


# ---------- Servicio que combina estado en BD + señal a la red ----------
class ServicioSuspensionesConRed(ServicioSuspensiones):
    def __init__(self, repo, red: ControladorRed, ips: dict[int, str]):
        super().__init__(repo)
        self.red = red
        self.ips = ips  # contrato_id -> IP (en producción vendría de la tabla de contratos)

    def suspender_morosos(self, ahora: datetime) -> list[int]:
        suspendidos = super().suspender_morosos(ahora)  # primero se guarda el estado
        for contrato_id in suspendidos:
            self._enviar(self.red.cortar, contrato_id)
        return suspendidos

    def reactivar_si_corresponde(self, contrato_id: int, ahora: datetime) -> bool:
        reactivado = super().reactivar_si_corresponde(contrato_id, ahora)
        if reactivado:
            self._enviar(self.red.activar, contrato_id)
        return reactivado

    def sincronizar(self) -> None:
        """Tarea periódica: corrige diferencias entre la BD y el router (p. ej. si el router estaba apagado)."""
        for c in self.repo.contratos.values():
            accion = self.red.cortar if c.estado == EstadoContrato.SUSPENDIDO else self.red.activar
            self._enviar(accion, c.id)

    def _enviar(self, accion, contrato_id: int) -> None:
        try:
            accion(self.ips[contrato_id])
        except Exception:
            # No se pierde el estado: 'sincronizar' lo reintentará en el siguiente ciclo
            log.exception("Fallo al enviar señal al router (contrato %s)", contrato_id)


# ---------- Pruebas ----------
class PruebasControlRed(unittest.TestCase):
    def setUp(self):
        self.venc = datetime(2026, 10, 10, 18, 0)
        self.repo = RepositorioMemoria()
        self.repo.contratos[1] = Contrato(1, cliente_id=1)
        self.factura = Factura(1, 1, Decimal("20"), self.venc)
        self.repo.facturas[1] = self.factura
        self.red = ControladorSimulado()
        self.servicio = ServicioSuspensionesConRed(self.repo, self.red, {1: "10.0.0.5"})

    def test_corta_internet_al_pasar_24h(self):
        self.servicio.suspender_morosos(self.venc + timedelta(hours=24))
        self.assertIn("10.0.0.5", self.red.cortados)

    def test_reactiva_internet_al_pagar(self):
        ahora = self.venc + timedelta(days=2)
        self.servicio.suspender_morosos(ahora)
        self.factura.pagado = Decimal("20")
        self.servicio.reactivar_si_corresponde(1, ahora)
        self.assertNotIn("10.0.0.5", self.red.cortados)

    def test_router_caido_se_corrige_al_sincronizar(self):
        class RouterCaido(ControladorSimulado):
            def cortar(self, ip):
                raise ConnectionError("router sin respuesta")

        servicio = ServicioSuspensionesConRed(self.repo, RouterCaido(), {1: "10.0.0.5"})
        servicio.suspender_morosos(self.venc + timedelta(hours=24))  # no debe lanzar excepción
        servicio.red = self.red  # el router vuelve a estar disponible
        servicio.sincronizar()
        self.assertIn("10.0.0.5", self.red.cortados)


if __name__ == "__main__":
    unittest.main()