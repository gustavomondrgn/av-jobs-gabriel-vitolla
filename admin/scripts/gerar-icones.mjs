/**
 * Gera `apple-icon.png` e `favicon.ico` a partir do `src/app/icon.svg`.
 *
 * A arte oficial é o SVG — os outros dois formatos existem só porque o iOS não
 * aceita SVG no ícone de tela inicial e navegadores antigos ainda procuram
 * `/favicon.ico` na raiz. Como são derivados, ninguém deve editá-los à mão:
 * troque o SVG e rode `npm run icones`.
 *
 * Usa o `sharp` que já vem junto com o Next — nenhuma dependência nova.
 */
import sharp from 'sharp';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const APP = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'app');
const svg = readFileSync(join(APP, 'icon.svg'));

// Ícone da tela inicial do iOS: 180×180 e sem transparência — o iOS não respeita
// o canal alfa e pintaria preto atrás do círculo.
await sharp(svg, { density: 384 })
  .resize(180, 180)
  .flatten({ background: '#073765' })
  .png()
  .toFile(join(APP, 'apple-icon.png'));

// `favicon.ico` com um único PNG 32×32 dentro. Todo navegador atual lê PNG
// dentro do contêiner ICO; é o mesmo truque que o GitHub usa.
const png = await sharp(svg, { density: 256 }).resize(32, 32).png().toBuffer();

const ico = Buffer.alloc(6 + 16 + png.length);
ico.writeUInt16LE(0, 0);            // reservado
ico.writeUInt16LE(1, 2);            // tipo: 1 = ícone
ico.writeUInt16LE(1, 4);            // quantidade de imagens
ico.writeUInt8(32, 6);              // largura
ico.writeUInt8(32, 7);              // altura
ico.writeUInt8(0, 8);               // cores da paleta (0 = sem paleta)
ico.writeUInt8(0, 9);               // reservado
ico.writeUInt16LE(1, 10);           // planos de cor
ico.writeUInt16LE(32, 12);          // bits por pixel
ico.writeUInt32LE(png.length, 14);  // tamanho da imagem
ico.writeUInt32LE(22, 18);          // onde a imagem começa
png.copy(ico, 22);
writeFileSync(join(APP, 'favicon.ico'), ico);

console.log('Ícones gerados: apple-icon.png (180×180) e favicon.ico (32×32).');
