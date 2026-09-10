import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import createCore from '../public/ffmpeg/ffmpeg-core.js';

// Supply the worker location while exercising the shipped WASM module under Node.
globalThis.self = { location: { href: 'http://localhost/ffmpeg/ffmpeg-core.js' } };
assert.equal(typeof createCore, 'function', 'The module worker needs an ESM default export');
const core = await createCore({
  wasmBinary: await readFile(new URL('../public/ffmpeg/ffmpeg-core.wasm', import.meta.url)),
});
const logs = [];
core.setLogger(({ message }) => logs.push(message));
const samples = 4410;
const wav = Buffer.alloc(44 + samples * 2);
wav.write('RIFF', 0);
wav.writeUInt32LE(wav.length - 8, 4);
wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20);
wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(44100, 24);
wav.writeUInt32LE(88200, 28);
wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34);
wav.write('data', 36);
wav.writeUInt32LE(samples * 2, 40);
core.FS.writeFile('input.wav', wav);
core.setTimeout(10000);
core.exec('-i', 'input.wav', '-vn', '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '320k', '-id3v2_version', '0', '-write_id3v1', '0', 'output.mp3');
assert.equal(core.ret, 0, logs.join('\n'));
const mp3 = core.FS.readFile('output.mp3');
assert.ok(mp3.length > 0);
assert.notEqual(Buffer.from(mp3.subarray(0, 3)).toString(), 'ID3');
assert.equal(mp3[0], 0xff, 'MP3 frame sync');
assert.equal(mp3[1] & 0x1e, 0x1a, 'MPEG-1 Layer III');
assert.equal(mp3[2] >> 4, 14, 'MPEG-1 Layer III bitrate index 14 is 320 kbps');
const decoder = await createCore({
  wasmBinary: await readFile(new URL('../public/ffmpeg/ffmpeg-core.wasm', import.meta.url)),
});
decoder.FS.writeFile('output.mp3', mp3);
decoder.setTimeout(10000);
decoder.exec('-i', 'output.mp3', '-f', 's16le', 'decoded.pcm');
assert.equal(decoder.ret, 0, 'The encoded MP3 must decode successfully');
assert.ok(decoder.FS.readFile('decoded.pcm').length >= samples * 2);
console.log('Shipped ESM/WASM encoder: valid 320 kbps MP3, no ID3 tags.');
