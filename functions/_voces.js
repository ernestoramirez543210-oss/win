// La voz estándar de los funnels (mujer) y la regla de que nunca falte.
//
// Fish Audio, sin reference_id, elige una voz cualquiera: los dos audios de una oferta
// salían con voces que nadie escogió, y el selector de Nueva oferta quedaba vacío porque
// la voz estándar solo se sembraba al abrir la pestaña Voces (y nunca si el formulario de
// APIs ya había guardado `voces: []`). Aquí vive la regla en un solo sitio, del lado del
// servidor, para que ninguna pestaña tenga que acordarse de sembrarla.

export const VOZ_ESTANDAR = {
  nombre: 'Voz estándar (mujer)',
  id: '',   // PON AQUI el id de la voz de Fish Audio que quieras por defecto (o eligela en /apis, «Voz por defecto»). Vacio = Fish elige una al azar
  genero: 'mujer',
};

const esFish = (audioApi) => ((audioApi && audioApi.tipo) || 'fishaudio') === 'fishaudio';

// audioApi con la lista de voces y la voz por defecto garantizadas: lo guardado si hay algo,
// la estándar si no. Con una API "compatible" los ids de Fish no significan nada, así que
// ahí no se siembra.
export function conVozEstandar(audioApi) {
  const a = audioApi && typeof audioApi === 'object' ? { ...audioApi } : {};
  if (!esFish(a)) return a;
  const voces = Array.isArray(a.voces) ? a.voces.filter((v) => v && v.id) : [];
  a.voces = voces.length ? voces : [{ ...VOZ_ESTANDAR }];
  a.vozId = vozPorDefecto(a);
  return a;
}

// La voz que se usa cuando nadie pidió una: la guardada en APIs o, en Fish, la estándar.
export function vozPorDefecto(audioApi) {
  const guardada = String((audioApi && audioApi.vozId) || '').trim();
  if (guardada) return guardada;
  return esFish(audioApi) ? VOZ_ESTANDAR.id : '';
}
