"""Las variaciones que pide el dueño desde /tablero › Variaciones («🤖 Hacerla en el servidor»), dentro de cada pasada del corredor del VPS (2026-11-13).

Lo usa corredor_vps.py (clase Corredor): este modulo no abre su propio candado ni su propia unidad; corre dentro de la pasada, con sus mismos limites, su pausa, su ventana de silencio,
su registro con las llaves tapadas y su forma de lanzar cada etapa (tiempo maximo y solo las llaves que esa etapa necesita). Hace dos cosas:

  1. el INDICE de originales: cada `variaciones.indice_horas` recorre OFERTAS LATAM en Drive (solo lectura; originales.py) y le manda al dash la lista de videos y lo que el servidor tiene
     encendido (si hace variaciones, su tope, el modo). Con eso /tablero muestra «🎞️ Abrir el original» en cada variacion y, al dueño, el boton.
  2. los PEDIDOS: lee los pendientes del dash (/api/variacion-editor, una lectura de KV si no hay nada), toma UNO por pasada, lo marca «tomado» y lo hace por etapas
     (variacion_auto.py en procesos aparte): original (Drive, solo lectura, comprobando que el archivo este dentro de OFERTAS LATAM) -> analisis -> gancho (lo unico que gasta, con el tope
     de corredor.json › variaciones.tope_usd) -> render -> unir -> revisar -> entrega (sube el video a OFERTAS LATAM › <carpeta de primer nivel del original> › VARIACIONES DEL SERVIDOR,
     nunca pisa) y le cuenta al dash «lista» con el enlace y el costo. Si algo falla, le cuenta «error» con una frase nuestra. Lo ya pagado no se vuelve a pagar si se pide otra vez.
Viene APAGADO (corredor.json › variaciones.activo = false): sin el si del dueño no toma pedidos ni gasta; el indice si se arma (solo lee Drive y escribe una clave en el dash).
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import originales as OR

ETAPAS_VAR = ("var_original", "var_analisis", "var_gancho", "var_render", "var_unir", "var_revisar", "var_entrega")
ETAPA_AL_DASH = {"var_original": "original", "var_analisis": "analisis", "var_gancho": "gancho", "var_render": "render", "var_unir": "unir", "var_revisar": "revisar", "var_entrega": "entrega"}
COSTO_ESTIMADO = {"clip": 0.07, "fish": 0.03, "original": 0.03}           # lo que dice la pagina antes de pedir (US$; el real lo mide cada corrida)
RE_SLUG_OFERTA = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
ETAPAS_AVISADAS = ("var_original", "var_gancho", "var_render", "var_revisar")   # las que se le cuentan al dash (cada aviso reescribe la fila: menos avisos, menos choques con el equipo)
RE_CARPETA = re.compile(r"^[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ][A-Za-z0-9ÁÉÍÓÚÑáéíóúñ ._-]{0,58}$")
EXT_PESADOS = ("original.mp4", "gancho_render.mp4")


class FallaVar(Exception):
    def __init__(self, motivo: str, espera: bool = False):
        super().__init__(motivo)
        self.motivo = motivo
        self.espera = espera


def validar_config(v: dict, errores: list[str], num) -> None:
    """Revisa corredor.json › variaciones (lo llama cargar_config de corredor_vps.py)."""
    if not isinstance(v.get("activo"), bool):
        errores.append("'variaciones.activo' es true o false")
    num("variaciones.tope_usd", v.get("tope_usd"), 0, 0.5)
    if v.get("modo") not in ("clip", "fish", "original"):
        errores.append("'variaciones.modo' es \"clip\" (el avatar del original dice el gancho), \"fish\" (nuestra voz + imagenes) u \"original\" (la voz del original + imagenes nuevas)")
    if not isinstance(v.get("voz"), str) or len(v.get("voz") or "") > 60:
        errores.append("'variaciones.voz' es el nombre de una voz guardada en el dash (o vacio = la de los flujos)")
    num("variaciones.imagenes", v.get("imagenes"), 1, 3, True)
    if not isinstance(v.get("revision_beto"), bool):
        errores.append("'variaciones.revision_beto' es true (Beto la mira y la marca Lista) o false (el servidor la deja Lista)")
    if not isinstance(v.get("carpeta"), str) or not RE_CARPETA.match(v.get("carpeta") or ""):
        errores.append("'variaciones.carpeta' es el nombre de la carpeta de Drive donde quedan (letras, numeros, espacios, . _ -)")
    num("variaciones.indice_horas", v.get("indice_horas"), 1, 168, True)
    num("variaciones.corte_min_seg", v.get("corte_min_seg"), 2, 8)
    num("variaciones.corte_max_seg", v.get("corte_max_seg"), 5, 15)
    co = v.get("clip_ofertas")
    if not isinstance(co, list) or len(co) > 50 or not all(isinstance(x, str) and RE_SLUG_OFERTA.match(x) for x in co):
        errores.append("'variaciones.clip_ofertas' es la lista de ofertas (como en «Costo por proyecto»: sermones-biblicos) cuyo original muestra un avatar AUTORIZADO o de IA: solo con ellas se usa el modo clip")
    try:
        if float(v.get("corte_max_seg")) <= float(v.get("corte_min_seg")):
            errores.append("'variaciones.corte_max_seg' debe ser mayor que 'corte_min_seg'")
    except (TypeError, ValueError):
        pass


class Variaciones:
    def __init__(self, corredor, dash: OR.DashVariaciones | None = None):
        self.c = corredor
        self.cfg = corredor.cfg["variaciones"]
        self.log = corredor.log
        self.dash = dash or OR.DashVariaciones(getattr(corredor.deps, "llamar_variaciones", None))
        self.dir_estado = corredor.estado_dir / "variaciones"
        self.dir_trabajo = corredor.raiz / "anuncios" / "_variaciones"

    # ---- lo que se le cuenta al dash del servidor (para la pagina)
    def servidor(self) -> dict:
        tope = round(float(self.cfg["tope_usd"]), 4)
        return {"activo": bool(self.cfg["activo"]) and tope > 0, "topeUsd": tope, "modo": self.cfg["modo"], "voz": self.cfg["voz"] or "la de los flujos",
                "revisionBeto": bool(self.cfg["revision_beto"]), "costoEstimadoUsd": COSTO_ESTIMADO.get(self.cfg["modo"], 0.05)}

    # ---- 1. el indice
    def indice_viejo(self) -> bool:
        try:
            d = json.loads((self.c.estado_dir / "indice_originales.json").read_text(encoding="utf-8"))
            hecho = datetime.fromisoformat(str(d.get("hechoEn")))
            if d.get("servidor") != self.servidor():
                return True                            # cambio lo que el servidor tiene encendido: la pagina debe saberlo ya
            if int(d.get("aplazados") or 0) > 0:
                return True                            # el dash guardo una parte (tope de grupos por envio): el resto en la pasada siguiente
        except (OSError, ValueError, TypeError):
            return True
        return self.c.deps.ahora() - hecho > timedelta(hours=int(self.cfg["indice_horas"]))

    def actualizar_indice(self, forzar: bool = False) -> None:
        if not forzar and not self.indice_viejo():
            return
        import drive_api as da

        raiz_id = da.id_de_carpeta(str(self.c.cfg["carpeta_ofertas"]))
        try:
            indice = OR.recorrer(self.c.drive(), raiz_id, registrar=self.log, reloj=self.c.deps.reloj, plazo_s=int(self.c.cfg["tiempos_max_min"]["indice"]) * 60)
        except da.ErrorPermiso:
            raise
        except Exception as e:  # noqa: BLE001 - el indice nunca tumba la pasada
            self.log(f"  índice de originales: no pude leer Drive ({e.__class__.__name__}: {str(e)[:120]}); lo intento en la próxima pasada")
            return
        if self.c.args.simular:
            self.log(f"  (simulación) le mandaría al dash {len(indice['archivos'])} video(s); no se manda nada")
            return
        try:
            r = self.dash.subir_indice(indice, self.servidor())
        except OR.ErrorDashVar as e:
            self.log(f"  índice de originales: el dash no lo recibió ({e}); lo intento en la próxima pasada")
            return
        self.c_guardar(self.c.estado_dir / "indice_originales.json", {"hechoEn": self.c.deps.ahora().isoformat(timespec="seconds"), "archivos": len(indice["archivos"]),
                                                                      "aceptados": r.get("total"), "avisos": indice["avisos"], "servidor": self.servidor(),
                                                                      "aplazados": int(r.get("aplazados") or 0)})
        self.log(f"  índice de originales: el dash aceptó {r.get('total')} de {len(indice['archivos'])} video(s)")

    def c_guardar(self, ruta: Path, datos) -> None:
        import corredor_vps as CV

        CV.guardar_json(ruta, datos)

    # ---- 2. los pedidos
    def estado_de(self, var_id: str) -> dict | None:
        try:
            d = json.loads((self.dir_estado / f"{var_id}.json").read_text(encoding="utf-8"))
            return d if isinstance(d, dict) and d.get("id") == var_id else None
        except (OSError, ValueError):
            return None

    def guardar_estado(self, st: dict) -> None:
        st["actualizado"] = self.c.deps.ahora().isoformat(timespec="seconds")
        self.c_guardar(self.dir_estado / f"{st['id']}.json", st)

    def propios_a_medias(self) -> list[str]:
        if not self.dir_estado.is_dir():
            return []
        salida = []
        for f in sorted(self.dir_estado.glob("*.json")):
            d = self.estado_de(f.stem)
            if d and d.get("estado") == "en-curso" and OR.RE_ID_VARIACION.match(f.stem):
                salida.append(f.stem)
        return salida

    def atender(self) -> str:
        """Una vuelta: indice si toca y, si las variaciones estan encendidas, UN pedido. Devuelve 'nada', 'hecha', 'error', 'espera' o 'cortado'. Nunca lanza (salvo el permiso de Drive)."""
        import drive_api as da

        try:
            self.actualizar_indice(forzar=bool(getattr(self.c.args, "indice", False)))
        except da.ErrorPermiso as e:
            self.log(f"DRIVE SIN PERMISO (índice de originales): {e}")
            return "error"
        a_medias = self.propios_a_medias()
        unico = getattr(self.c.args, "variacion", None)
        try:
            if unico:
                # La orden a mano del dueño es para ESA variacion: no se sigue con otra a medias (con el tope de esta) y se lee directo (la lista de pendientes trae 2 como mucho).
                a_medias = [x for x in a_medias if x == unico]
                pendientes = []
                if not a_medias:
                    pedido, edicion = self.dash.uno(unico)
                    if pedido is None or str((edicion or {}).get("estado")) != "pendiente":
                        self.log(f"Variaciones: {unico} no está pedida al servidor (estado: {(edicion or {}).get('estado') or 'sin pedido'}).")
                        return "nada"
                    pendientes = [pedido]
            else:
                pendientes = [] if a_medias else self.dash.pendientes()
        except OR.ErrorDashVar as e:
            self.log(f"Variaciones: el dash no contestó ({e})")
            return "nada" if e.codigo == 404 else "error" if e.config else "espera"
        if not a_medias and not pendientes:
            self.log("Variaciones: ningún pedido.")
            return "nada"
        if not (self.cfg["activo"] and float(self.cfg["tope_usd"]) > 0) and not self.ok_de_esta_corrida():
            self.log(f"Variaciones: hay {len(pendientes) + len(a_medias)} pedido(s), pero el servidor tiene las variaciones APAGADAS (corredor.json › variaciones: activo y tope_usd los decide el dueño). No toco nada.")
            return "nada"
        if a_medias:
            try:
                pedido, edicion = self.dash.uno(a_medias[0])
            except OR.ErrorDashVar as e:
                if e.codigo != 404:
                    self.log(f"Variaciones: no pude releer la variación {a_medias[0]} ({e})")
                    return "espera"
                pedido, edicion = None, {}            # la borraron en el tablero: se deja (si no, la cola se traba esperandola)
            if pedido is None or str((edicion or {}).get("estado")) not in ("tomado", "en-curso"):
                st = self.estado_de(a_medias[0]) or {"id": a_medias[0]}
                st["estado"], st["nota"] = "abandonada", f"el dash ya no la tiene en curso ({(edicion or {}).get('estado') or 'no existe'})"
                self.guardar_estado(st)
                self.log(f"Variaciones: {a_medias[0]} ya no está en curso en el dash; la dejo.")
                return "nada"
            return self.procesar(pedido, propio=True)
        return self.procesar(pendientes[0], propio=False)

    def clip_autorizado(self, pedido: dict) -> bool:
        """El modo clip pone palabras nuevas en la boca de la persona del original: solo con las ofertas que el dueño puso en clip_ofertas (avatar autorizado o de IA)."""
        return OR.slug_de(pedido.get("oferta") or "") in (self.cfg.get("clip_ofertas") or [])

    def ok_de_esta_corrida(self) -> bool:
        """`--variacion ID --tope-usd X` (a mano, con el si del dueño para ESA variacion) la hace aunque las variaciones esten apagadas, con ese tope (nunca mas de US$ 0,50)."""
        return bool(getattr(self.c.args, "variacion", None)) and getattr(self.c.args, "tope_usd", None) is not None and float(self.c.args.tope_usd) > 0

    def tope(self) -> float:
        if self.ok_de_esta_corrida():
            return round(min(0.5, float(self.c.args.tope_usd)), 4)
        return round(float(self.cfg["tope_usd"]), 4)

    def evaluar(self, pedido: dict) -> str:
        """'' si se puede hacer ahora; si no, por que no (y no se toca el pedido)."""
        if pedido["cambio"] not in ("", "inicio"):
            return "esta variación cambia también el cierre o el formato: la hace Beto"
        if self.cfg["modo"] == "clip" and not self.clip_autorizado(pedido):
            return ("esta variación usaría el modo clip (la persona del original dice el gancho nuevo con IA) y la oferta no está en corredor.json › variaciones.clip_ofertas "
                    "(solo ofertas cuyo avatar está autorizado o es de IA): cambie el modo a fish o autorice la oferta")
        if not pedido["gancho"] and self.cfg["modo"] != "original":
            return "la variación no trae gancho"
        if float(self.c.cfg["topes"]["usd_por_dia"]) - self.c.gasto_hoy() < float(COSTO_ESTIMADO.get(self.cfg["modo"], 0.05)):
            return "se acabó el tope de gasto de hoy del corredor (corredor.json › topes.usd_por_dia)"
        if self.c.minutos_restantes() < 25:
            return "no queda tiempo en esta pasada"
        if not self.c.hay_recursos():
            return "el servidor está ocupado (memoria, carga o disco)"
        return ""

    def procesar(self, pedido: dict, propio: bool) -> str:
        c, log = self.c, self.log
        vid = pedido["id"]
        motivo = self.evaluar(pedido)
        if c.args.simular:
            log(f"- Variación {pedido['numero']} de «{pedido['oferta']}» (sale del {pedido['base']}): " + (f"ESPERA · {motivo}" if motivo else f"la haría (modo {self.cfg['modo']}, tope US$ {self.tope():.2f}); no se toca nada"))
            return "nada"
        if motivo:
            if motivo.startswith("esta variación") or motivo.startswith("la variación"):
                self.contar_error(pedido, f"El servidor no la hace: {motivo}.")
                return "error"
            log(f"- Variación {pedido['numero']}: ESPERA · {motivo}")
            return "espera"
        st = self.estado_de(vid)
        if not propio:
            try:
                self.avisar(pedido, "tomado")
            except OR.ErrorDashVar as e:
                if e.codigo in (404, 409):
                    log(f"- Variación {pedido['numero']}: el dash dice que ya no está por hacer ({e}); la salto")
                    return "nada"
                log(f"- Variación {pedido['numero']}: no la pude tomar ({e}); la dejo")
                return "espera"
            if st is None or st.get("estado") != "en-curso":
                st = {"id": vid, "creado": c.deps.ahora().isoformat(timespec="seconds"), "etapas": {}, "gasto_usd": 0.0}
            st["etapas"] = {k: v for k, v in (st.get("etapas") or {}).items() if k in ("var_original",)}     # un pedido nuevo rehace todo menos la bajada (los pagos ya hechos se reusan en la carpeta)
        st.update({"estado": "en-curso", "numero": pedido["numero"], "oferta": pedido["oferta"], "corrida": c.corrida, "nota": "", "intento": pedido.get("intento", 0)})
        self.guardar_estado(st)
        c.env_hijos = c.armar_entorno()          # el entorno de los hijos sin lo que nunca deben ver (la llave del dash solo la recibe var_gancho: LLAVES_POR_ETAPA)
        carpeta = self.dir_trabajo / vid
        carpeta.mkdir(parents=True, exist_ok=True)
        datos = {**pedido, "producto": OR.slug_de(pedido["oferta"]), "anuncio": OR.id_de_costos(pedido["numero"])}
        c_json = carpeta / "pedido.json"
        self.c_guardar(c_json, datos)
        log(f"=== Variación {pedido['numero']} de «{pedido['oferta']}» · sale de «{pedido['original']['nombre']}» · modo {self.cfg['modo']} · tope US$ {self.tope():.2f} ===")
        etapas = [("var_original", self.e_original), ("var_analisis", self.e_analisis), ("var_gancho", self.e_gancho), ("var_render", self.e_render), ("var_unir", self.e_unir),
                  ("var_revisar", self.e_revisar), ("var_entrega", self.e_entrega)]
        import corredor_vps as CV
        from anuncio_comun import gastado_en_anuncio

        ctx = {"pedido": datos, "carpeta": carpeta, "st": st}
        try:
            for nombre, fn in etapas:
                if c.cortar:
                    raise CV.Cortado()
                if c.pausa():
                    raise CV.Pausado()
                if c.en_silencio():
                    raise CV.Silencio()
                if (st["etapas"].get(nombre) or {}).get("ok"):
                    log(f"- {nombre}: ya estaba hecha")
                    continue
                try:
                    if nombre in ETAPAS_AVISADAS:
                        self.avisar(pedido, "en-curso", etapa=ETAPA_AL_DASH[nombre])
                except OR.ErrorDashVar as e:
                    if e.codigo in (404, 409):     # la borraron, el dueño la volvio a pedir (otro intento) o ya no esta por producir: este trabajo no sigue
                        st["estado"], st["nota"] = "abandonada", f"el dash ya no espera este intento ({e})"
                        self.guardar_estado(st)
                        log(f"Variación {pedido['numero']}: el dash ya no espera este intento ({e}); la dejo.")
                        return "nada"
                    log(f"  (no pude contarle al dash la etapa: {e})")
                antes = gastado_en_anuncio(datos["producto"], datos["anuncio"])
                try:
                    nota = fn(ctx) or ""
                finally:
                    despues = gastado_en_anuncio(datos["producto"], datos["anuncio"])
                    c.sumar_gasto(max(0.0, despues - antes))
                    st["gasto_usd"] = round(despues, 6)
                st["etapas"][nombre] = {"ok": True, "t": c.deps.ahora().isoformat(timespec="seconds"), "nota": nota[:200]}
                self.guardar_estado(st)
            st["estado"] = "entregada"
            self.guardar_estado(st)
            return "hecha"
        except (CV.Pausado, CV.Silencio, CV.Cortado) as e:
            st["nota"] = f"detenida entre etapas ({e.__class__.__name__}); se retoma en la siguiente pasada"
            self.guardar_estado(st)
            log(f"Variación {pedido['numero']}: me detengo entre etapas (se retoma en la siguiente pasada).")
            return "cortado"
        except FallaVar as e:
            st["estado"], st["nota"] = "error", e.motivo[:300]
            self.guardar_estado(st)
            log(f"FALLA en la variación {pedido['numero']}: {e.motivo}")
            self.contar_error(pedido, f"El servidor no pudo hacer la variación: {e.motivo}", costo=st.get("gasto_usd"))
            return "error"
        except CV.FallaDeEtapa as e:
            st["estado"], st["nota"] = "error", e.motivo[:300]
            self.guardar_estado(st)
            log(f"FALLA en la variación {pedido['numero']}: {e.motivo}")
            self.contar_error(pedido, f"El servidor no pudo hacer la variación: {e.motivo}", costo=st.get("gasto_usd"))
            return "error"
        except OR.ErrorDashVar as e:
            if e.codigo in (404, 409):
                st["estado"], st["nota"] = "abandonada", f"el dash ya no espera este intento ({e})"
                self.guardar_estado(st)
                log(f"Variación {pedido['numero']}: el dash ya no espera este intento ({e}); la dejo.")
                return "nada"
            st["nota"] = f"el dash no contestó ({e}); se retoma"
            self.guardar_estado(st)
            log(f"Variación {pedido['numero']}: el dash no contestó ({e}); se retoma en la siguiente pasada")
            return "espera"

    def avisar(self, pedido: dict, estado: str, **campos) -> dict:
        """Un aviso al dash sobre ESTE pedido, con su numero de intento (el dash rechaza con 409 el aviso de un intento anterior)."""
        return self.dash.avance(pedido["id"], estado, intento=int(pedido.get("intento") or 0), **campos)

    def contar_error(self, pedido: dict, nota: str, costo=None) -> None:
        nota = self.log.limpiar(nota) if hasattr(self.log, "limpiar") else nota          # la nota la ve todo el equipo: con las llaves tapadas, como el registro
        try:
            self.avisar(pedido, "error", nota=nota[:290], costoUsd=round(float(costo), 4) if costo else None)
        except OR.ErrorDashVar as e:
            self.log(f"  (no pude contarle el error al dash: {e})")

    # ---- etapas
    def _correr(self, nombre: str, *args: str) -> None:
        r = self.c.correr_etapa(nombre, self.c.py("variacion_auto.py", *args))
        if r.codigo != 0:
            ultima = next((ln for ln in reversed(r.ultimas) if ln.strip().startswith("ERROR:")), "")
            motivo = ultima.strip()[6:].strip() if ultima else f"la etapa {ETAPA_AL_DASH.get(nombre, nombre)} terminó con código {r.codigo}"
            raise FallaVar(motivo[:280])

    def e_original(self, ctx: dict) -> str:
        """Baja el original de Drive (solo lectura, tamaño y MD5 comprobados) SOLO si esta dentro de OFERTAS LATAM: un id que manda el dash no abre cualquier archivo de Drive."""
        import drive_api as da
        import espejo_drive as ed

        p, carpeta = ctx["pedido"], ctx["carpeta"]
        drive = self.c.drive()
        raiz_id = da.id_de_carpeta(str(self.c.cfg["carpeta_ofertas"]))
        cadena = OR.ancestros(drive, p["original"]["id"], raiz_id)
        if not cadena:
            raise FallaVar("el video original no está dentro de OFERTAS LATAM en Drive (o se borró)")
        if any(OR.SALTAR.match(str(c.get("nombre") or "")) for c in cadena):
            raise FallaVar("el video está en una carpeta que no es de anuncios nuestros (B-rolls, REFERENCIA, AVATAR…)")
        ctx["st"]["raiz"] = cadena[-1]
        self.guardar_estado(ctx["st"])
        f = drive.datos_de(p["original"]["id"])
        if f.get("trashed") or not OR.es_video(str(f.get("name") or "")) or not str(f.get("mimeType") or "").startswith("video/"):
            raise FallaVar("el original ya no es un video en Drive")
        esp = self.c.espejo()
        try:
            res = esp._bajar_archivo({**f, "name": f.get("name")}, carpeta / "original.mp4", self.c.deps.reloj() + int(self.c.cfg["tiempos_max_min"]["var_original"]) * 60)
        except ed.ErrorEspejo as e:
            raise FallaVar(f"no pude bajar el original de Drive ({str(e)[:120]})") from None
        if not OR.es_mp4_de_verdad(carpeta / "original.mp4"):
            (carpeta / "original.mp4").unlink(missing_ok=True)
            raise FallaVar("lo que bajó de Drive no es un video MP4/MOV de verdad (solo tiene el nombre)")
        return f"original {res} ({int(f.get('size') or 0) / 1048576:.0f} MB) de «{cadena[0]['nombre'] if cadena else ''}»"

    def e_analisis(self, ctx: dict) -> str:
        self._correr("var_analisis", "analizar", str(ctx["carpeta"]), "--corte-min", f"{float(self.cfg['corte_min_seg']):g}", "--corte-max", f"{float(self.cfg['corte_max_seg']):g}")
        a = json.loads((ctx["carpeta"] / "analisis.json").read_text(encoding="utf-8"))
        return f"corte en {a['corte']:.2f} s"

    def e_gancho(self, ctx: dict) -> str:
        tope = round(min(self.tope(), float(self.c.cfg["topes"]["usd_por_dia"]) - self.c.gasto_hoy()), 4)
        if tope <= 0.005:
            raise FallaVar("no queda tope de gasto (el de la variación o el del día)", espera=True)
        args = ["gancho", str(ctx["carpeta"]), "--tope-usd", f"{tope:.4f}", "--modo", self.cfg["modo"], "--imagenes", str(int(self.cfg["imagenes"]))]
        if self.cfg["modo"] == "clip":
            if not self.clip_autorizado(ctx["pedido"]):
                raise FallaVar("el modo clip solo va con las ofertas de corredor.json › variaciones.clip_ofertas")
            args.append("--clip-autorizado")
        if self.cfg["voz"]:
            args += ["--voz", self.cfg["voz"]]
        self._correr("var_gancho", *args)
        return "inicio nuevo listo"

    def e_render(self, ctx: dict) -> str:
        self._correr("var_render", "render", str(ctx["carpeta"]))
        return "inicio renderizado"

    def e_unir(self, ctx: dict) -> str:
        self._correr("var_unir", "unir", str(ctx["carpeta"]))
        return "unida"

    def e_revisar(self, ctx: dict) -> str:
        r = self.c.correr_etapa("var_revisar", self.c.py("variacion_auto.py", "revisar", str(ctx["carpeta"])))
        try:
            rev = json.loads((ctx["carpeta"] / "revision.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rev = {}
        if r.codigo != 0 or not rev.get("ok"):
            malas = [k for k, v in (rev.get("compuertas") or {}).items() if isinstance(v, dict) and not v.get("ok")]
            raise FallaVar("el video quedó pero no pasó el control de calidad: " + (", ".join(m.replace("_", " ") for m in malas[:5]) or f"código {r.codigo}"))
        return "pasó el control de calidad"

    def e_entrega(self, ctx: dict) -> str:
        p, carpeta, st = ctx["pedido"], ctx["carpeta"], ctx["st"]
        union = json.loads((carpeta / "union.json").read_text(encoding="utf-8"))
        rev = json.loads((carpeta / "revision.json").read_text(encoding="utf-8"))
        video = carpeta / "final" / str(union["archivo"])
        raiz = st.get("raiz") or {}
        if not video.is_file() or not OR.RE_ID_DRIVE.match(str(raiz.get("id") or "")):
            raise FallaVar("no encuentro el video final o la carpeta de Drive donde dejarlo")
        sub = self.c.espejo().subir_anuncio({"id": raiz["id"], "name": raiz.get("nombre", "")}, video, carpeta=str(self.cfg["carpeta"]))
        self.log(f"  Drive: {sub['name']} ({sub['size'] / 1048576:.1f} MB) {sub['estado']} en {raiz.get('nombre')}/{self.cfg['carpeta']} -> {sub['enlace']}")
        from anuncio_comun import gastado_en_anuncio

        costo = round(gastado_en_anuncio(p["producto"], p["anuncio"]), 4)
        st["entrega"] = {"archivo": sub["name"], "id": sub["id"], "enlace": sub["enlace"], "costo_usd": costo}
        self.guardar_estado(st)
        self.avisar(p, "lista", driveId=str(sub["id"]), archivo=sub["name"], costoUsd=costo, corteSeg=round(float(union["corte"]), 2),
                    ganchoSeg=round(float(union["inicio_seg"]), 2), marcarLista=not bool(self.cfg["revision_beto"]))
        return f"entregada: {sub['name']} · US$ {costo:.3f} · {'; '.join(k for k, v in rev.get('compuertas', {}).items() if v.get('ok'))[:80]}"

    # ---- limpieza (lo pesado de las variaciones entregadas hace mas de N dias; el video final ya esta en Drive)
    def limpiar(self) -> None:
        if not self.dir_estado.is_dir():
            return
        limite = self.c.deps.ahora() - timedelta(days=int(self.c.cfg["dias_guardar_trabajo"]))
        for f in self.dir_estado.glob("*.json"):
            st = self.estado_de(f.stem)
            if not st or st.get("estado") not in ("entregada", "error", "abandonada") or st.get("trabajo_limpio"):
                continue
            try:
                if datetime.fromisoformat(str(st.get("actualizado"))) > limite:
                    continue
            except ValueError:
                continue
            carpeta = self.dir_trabajo / f.stem
            for n in EXT_PESADOS:
                (carpeta / n).unlink(missing_ok=True)
            for sub in ("final", "revision"):
                shutil.rmtree(carpeta / sub, ignore_errors=True)
            # gancho/ se queda (pesa poco): ahi estan la voz, las imagenes y el clip ya PAGADOS; si se vuelve a pedir la misma variacion, no se pagan otra vez
            st["trabajo_limpio"] = True
            self.guardar_estado(st)
