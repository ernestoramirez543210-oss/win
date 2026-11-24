import {Config} from "@remotion/cli/config";
import {enableTailwind} from "@remotion/tailwind-v4";

// Esta configuracion la leen `remotion studio` y los comandos `remotion ...`.
// scripts/remotion.mjs (el que usa Claude para renderizar) aplica el mismo enableTailwind a mano.
Config.setOverwriteOutput(true);
Config.overrideWebpackConfig((config) => enableTailwind(config));
