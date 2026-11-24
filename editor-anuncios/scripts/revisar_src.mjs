// Revision por ARBOL SINTACTICO de la parte visual (src/**/*.ts y *.tsx) antes de instalarla (la corre la puerta de mejoras, scripts/editor_promover.py, DENTRO de la caja sin red ni llaves).
// Complementa la busqueda de texto: el arbol resuelve los escapes de unicode de los nombres (require), no se deja engañar por comentarios ni cadenas y ve cada import.
//
//   node scripts/revisar_src.mjs <archivo.tsx> [...]      imprime una linea  RESULTADO_JSON {"problemas": [...], "imports": [[archivo, especificador], ...]}
//
// Lo que busca: lo que lee archivos al empaquetar (require y sus .context, import() dinamico, import.meta, define de AMD, internos de webpack, `import x = require()`), lo que sale a la red
// o ejecuta texto (fetch, XMLHttpRequest, WebSocket, eval, Function, setTimeout con texto, constructor, __proto__) y los globales del navegador por los que se llega a ellos (window, self, globalThis...).
// Un video no necesita nada de eso. No es una garantia total (el render de produccion tiene red): es la barrera de las vias conocidas.
import fs from "node:fs";
import ts from "typescript";

// prohibidos en cualquier lugar (tambien como nombre de propiedad o como texto entre comillas: window["fetch"])
const EN_CUALQUIER_LUGAR = new Set(["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts", "Worker", "SharedWorker", "constructor", "__proto__", "localStorage", "sessionStorage", "indexedDB", "location", "open"]);
// prohibidos como referencia (un nombre de propiedad CSS como `top: 0` no cuenta)
const COMO_REFERENCIA = new Set(["window", "self", "globalThis", "location", "top", "parent", "require", "module", "exports", "define", "process", "global", "eval", "Function", "Reflect", "Proxy", "__dirname", "__filename"]);

// alias de window que tambien son nombres comunes de variables (`frames`, `top`, `parent`): solo cuentan si el archivo NO los declara el mismo
const ALIAS_SI_NO_SE_DECLARAN = new Set(["frames"]);

const salida = {problemas: [], imports: []};
const mal = (archivo, nodo, texto) => {
  const {line} = nodo.getSourceFile().getLineAndCharacterOfPosition(nodo.getStart());
  salida.problemas.push(`${archivo}:${line + 1}: ${texto}`);
};

function esNombreDePropiedad(n) {
  const p = n.parent;
  if (!p) return false;
  return (
    (ts.isPropertyAccessExpression(p) && p.name === n) ||
    (ts.isPropertyAssignment(p) && p.name === n) ||
    (ts.isMethodDeclaration(p) && p.name === n) ||
    (ts.isPropertySignature(p) && p.name === n) ||
    (ts.isPropertyDeclaration(p) && p.name === n) ||
    (ts.isJsxAttribute(p) && p.name === n) ||
    (ts.isQualifiedName(p) && p.right === n) ||
    (ts.isBindingElement(p) && p.propertyName === n)
  );
}

for (const archivo of process.argv.slice(2)) {
  const texto = fs.readFileSync(archivo, "utf8");
  const sf = ts.createSourceFile(archivo, texto, ts.ScriptTarget.Latest, true, archivo.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  if ((sf.parseDiagnostics ?? []).length > 0) salida.problemas.push(`${archivo}: no se puede leer como TypeScript (error de sintaxis)`);
  const declarados = new Set();
  const recoger = (n) => {
    if ((ts.isVariableDeclaration(n) || ts.isParameter(n) || ts.isBindingElement(n) || ts.isFunctionDeclaration(n)) && n.name && ts.isIdentifier(n.name)) declarados.add(n.name.text);
    ts.forEachChild(n, recoger);
  };
  recoger(sf);
  const visitar = (n) => {
    if (ts.isIdentifier(n) || ts.isPrivateIdentifier(n)) {
      const nombre = n.text;
      if (EN_CUALQUIER_LUGAR.has(nombre)) mal(archivo, n, `usa «${nombre}»`);
      else if (COMO_REFERENCIA.has(nombre) && !esNombreDePropiedad(n)) mal(archivo, n, `usa «${nombre}»`);
      else if (ALIAS_SI_NO_SE_DECLARAN.has(nombre) && !esNombreDePropiedad(n) && !declarados.has(nombre)) mal(archivo, n, `usa «${nombre}» (alias de window) sin declararlo`);
      else if (nombre.startsWith("__webpack") || nombre.startsWith("__non_webpack")) mal(archivo, n, `usa «${nombre}» (interno de webpack)`);
    } else if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      if (EN_CUALQUIER_LUGAR.has(n.text) || n.text.startsWith("__webpack")) mal(archivo, n, `nombra «${n.text.slice(0, 30)}» entre comillas`);
    } else if (ts.isCallExpression(n)) {
      if (n.expression.kind === ts.SyntaxKind.ImportKeyword) mal(archivo, n, "import() dinamico");
      const nombre = ts.isIdentifier(n.expression) ? n.expression.text : "";
      if ((nombre === "setTimeout" || nombre === "setInterval") && n.arguments.length > 0 && !ts.isArrowFunction(n.arguments[0]) && !ts.isFunctionExpression(n.arguments[0]) && !ts.isIdentifier(n.arguments[0])) {
        mal(archivo, n, `${nombre} con algo que no es una funcion (texto ejecutable)`);
      }
    } else if (ts.isMetaProperty(n)) {
      mal(archivo, n, "import.meta");
    } else if (ts.isImportEqualsDeclaration(n)) {
      mal(archivo, n, "import x = require(...)");
    } else if (ts.isImportTypeNode(n)) {
      mal(archivo, n, "import(...) en un tipo");
    } else if ((ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) && n.moduleSpecifier) {
      if (ts.isStringLiteral(n.moduleSpecifier)) salida.imports.push([archivo, n.moduleSpecifier.text]);
      else mal(archivo, n, "import con un especificador que no es un texto fijo");
    }
    ts.forEachChild(n, visitar);
  };
  visitar(sf);
}
console.log("RESULTADO_JSON " + JSON.stringify(salida));
