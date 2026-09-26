// Folds dist-mock/ into one self-contained file: ../artifacts/setup-mock.html
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";

const dir = "dist-mock/assets";
const files = readdirSync(dir);
const read = (ext) => files.filter((f) => f.endsWith(ext)).map((f) => readFileSync(`${dir}/${f}`, "utf8")).join("\n");
const css = read(".css");
// A closing script tag inside the bundle would end the inline script early.
const js = read(".js").replaceAll("</script", "<\\/script");

let html = readFileSync("dist-mock/mock/index.html", "utf8");
html = html.replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/, "");
html = html.replace(/<link rel="stylesheet"[^>]*>/, "");
// Function replacers: the bundle is full of `$&` and `$'`, which a string replacement expands.
html = html.replace("</head>", () => `<style>${css}</style>\n</head>`);
html = html.replace("</body>", () => `<script type="module">${js}</script>\n</body>`);

mkdirSync("../artifacts", { recursive: true });
writeFileSync("../artifacts/setup-mock.html", html);
console.log(`../artifacts/setup-mock.html  ${(html.length / 1024).toFixed(0)} KB`);
