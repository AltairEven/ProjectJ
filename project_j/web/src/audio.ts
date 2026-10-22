// Audio bytes between the page and the backend: streamed PCM in, WAV recordings out.

// Little-endian 16-bit PCM to [-1, 1); an odd trailing byte waits for the next network chunk.
export function decodePcm16(chunk: Uint8Array, carry: number | null) {
  let bytes = chunk
  if (carry !== null) {
    bytes = new Uint8Array(chunk.length + 1)
    bytes[0] = carry
    bytes.set(chunk, 1)
  }
  const count = bytes.length >> 1
  const view = new DataView(bytes.buffer, bytes.byteOffset, count * 2)
  const samples = new Float32Array(count)
  for (let i = 0; i < count; i++) samples[i] = view.getInt16(i * 2, true) / 32768
  return { samples, carry: bytes.length % 2 ? bytes[bytes.length - 1] : null }
}

export function toMono(channels: Float32Array[]): Float32Array {
  const mono = new Float32Array(channels[0]?.length ?? 0)
  for (const channel of channels) channel.forEach((value, i) => { mono[i] += value / channels.length })
  return mono
}

// Mono PCM16 WAV, the format the local pronunciation scorer reads (16 kHz from the page).
export function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array<ArrayBuffer> {
  const wav = new Uint8Array(44 + samples.length * 2)
  const view = new DataView(wav.buffer)
  const text = (offset: number, value: string) => wav.set(new TextEncoder().encode(value), offset)
  text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVEfmt ')
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true); view.setUint16(34, 16, true)
  text(36, 'data'); view.setUint32(40, samples.length * 2, true)
  samples.forEach((value, i) => view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, value)) * 32767), true))
  return wav
}
