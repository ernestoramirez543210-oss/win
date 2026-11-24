# Instrucciones para agentes — Suite Mi Negocio

Las reglas generales del proyecto están en **`CLAUDE.md`** (prioridad #1: hacer bien el
trabajo) y cómo se cambian las cosas con revisión independiente, en **`POLITICA-CAMBIOS.md`**.
Este archivo agrega **una regla fija más**, que no se negocia y aplica a todo lo que se
construya de aquí en adelante.

---

## REGLA FIJA: todo lo que se construya se ve bien en celular

El dueño usa la suite **desde el teléfono** tanto como desde la PC: mira la torre en el
celular, abre los dashboards en el celular, manda el link por WhatsApp y lo abre desde el
celular. Un layout que solo se ve bien en una pantalla grande **no está terminado**.

Aplica a **todo**: páginas nuevas, pestañas nuevas del menú, tarjetas, tablas, formularios,
modales, botones, generadores de HTML (entregables, ebooks), y cualquier cambio de CSS en
`templates/`. No hay excepción por "es una página interna" ni por "esto lo abro yo".

### Antes de dar algo por listo

1. Correr el verificador:
   `powershell -File scripts\Verificar-Movil.ps1`
   Sale solo desde `scripts\Build-Suite.ps1`, así que una página nueva sin viewport avisa en
   la misma corrida del build. **Cero ERRORES es obligatorio**; los AVISOS se arreglan o se
   justifican.
2. Medir la página de verdad en el navegador con el ancho del teléfono (ver método abajo).
   El verificador no reemplaza la medición: encuentra los patrones conocidos, no los bugs
   nuevos.
3. Recién con las dos cosas en verde, commitear.

### El método de medición (no se adivina mirando el código)

El navegador a ancho de celular (360–390px, o menos) y este chequeo sobre la página
cargada. Script de referencia, ya usado en esta suite:

```js
const W = window.innerWidth, de = document.documentElement;
// 1) ¿la página desborda? Si scrollWidth > clientWidth, hay algo que se sale.
console.log(de.scrollWidth, de.clientWidth);
// 2) ¿qué se sale, y por qué? Se ignoran los que viven dentro de un contenedor con
//    overflow-x propio (esos scrollean, que es lo correcto).
for (const el of document.querySelectorAll('body *')) {
  const r = el.getBoundingClientRect();
  if (!r.width && !r.height) continue;
  if (r.right > W + 2) {
    let p = el.parentElement, scroller = null;
    while (p) { const cs = getComputedStyle(p);
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') { scroller = p; break; }
      p = p.parentElement; }
    if (!scroller || scroller.scrollWidth <= scroller.clientWidth + 2) {
      console.log('CORTADO SIN SCROLL:', el.tagName, el.className, Math.round(r.right));
    }
  }
}
```

Un elemento que se sale **y no tiene contenedor con scroll** es contenido cortado: el usuario
no puede verlo ni desplazarlo. Eso es el bug; un `scrollWidth` mayor sin `overflow-x` es la
misma enfermedad vista desde la página.

### Los seis errores que ya nos costaron caro (no repetirlos)

1. **Falta la meta viewport** → el teléfono renderiza a 980px y encoge todo. Fue la queja
   original del dueño, con foto de pantalla incluida. `pipeline`, `salud` y `tasas`
   estuvieron meses así. La pone la plantilla, no el bloque `suite-*`: si construís una
   página nueva, va en su `<head>`.
2. **Item de grid/flex sin `min-width:0`** → no puede encogerse por debajo del ancho natural
   de su contenido (una tabla, un `pre`), empuja el contenedor y el `overflow-x:auto` de
   adentro nunca se activa. El dashboard tenía las tablas de rentabilidad cortadas así:
   469px de tabla dentro de un hueco de 131px, sin forma de desplazarla. Se arregla con
   `min-width:0` en el ítem.
3. **Grillas de 3+ columnas sin `minmax()`/`auto-fit`** → el caso del embudo: 3 columnas que
   en un teléfono sumaban 260px dentro de 131px reales y se recortaban. Si no tiene
   breakpoint que lo apile, no está listo.
4. **Flex sin `wrap`** → los botones de modo del Cerebro se salían de su propia tarjeta
   (319px dentro de un contenedor de 131px) y estiraban la página entera. Si puede tener
   más de dos hijos, `flex-wrap:wrap`.
5. **Área de toque chica** → un interruptor de 18px de alto no se puede tocar con el dedo.
   Mínimo ~40px de alto en móvil; si el diseño debe verse chico, agrandar el área con un
   pseudo-elemento invisible (`.sw::after { inset:-11px }`), no el dibujo.
6. **Ancho fijo en píxeles sin breakpoint** → solo se permite si el bloque se escala entero
   después (como el lienzo A5 de los ebooks, 768×1024 que se ve con `transform:scale`). En
   ese caso se marca la línea con **`movil-ok`** y se explica por qué, para que el
   verificador no la vuelva a marcar.

### Detalles que hacen la diferencia en el teléfono

- Padding de la página: en escritorio 24–26px, en el teléfono 12–14px. Los 24px se comen un
  8% de una pantalla de 360px.
- Si achicás el nav a 56px, el contenido tiene que respetarlo (`body { padding-left:56px }`);
  el menú de la suite ya lo hace en `@media (max-width:860px)`.
- `100vw` incluye la barra de scroll: preferir `100%` o `min-width:0`.
- Probar con el contenido **real**: los textos largos (nombres de oferta, títulos de ebook)
  son los que rompen las grillas, no los cortos.

### Definition of done (para cualquier cambio en la suite)

- [ ] `scripts\Verificar-Movil.ps1` sin ERRORES.
- [ ] Página medida en el navegador a ancho de celular: sin contenido cortado, sin desborde
      horizontal de la página, targets táctiles ≥ ~40px.
- [ ] Build corrido (`build-*.ps1` de la página → `scripts\Build-Suite.ps1`) y el HTML
      construido verificado, no solo la plantilla.
