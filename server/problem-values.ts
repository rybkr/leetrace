import { inflateSync } from 'node:zlib';
import type { JsonValue } from './problems.js';

const MAX_VALUE_BYTES = 64 * 1024 * 1024;
interface CompressedValue { $json: string; bytes: number }

function compressedValue(value: JsonValue): CompressedValue | undefined {
  if (value === null || typeof value !== 'object' || Array.isArray(value) || !Object.hasOwn(value, '$json')) {
    return undefined;
  }
  if (Object.keys(value).length !== 2 || typeof value.$json !== 'string' || typeof value.bytes !== 'number'
      || !Number.isInteger(value.bytes) || value.bytes < 1 || value.bytes > MAX_VALUE_BYTES) {
    throw new Error('Invalid compressed problem value metadata.');
  }
  return { $json: value.$json, bytes: value.bytes };
}

export function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  if (typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  if (Object.hasOwn(record, '$bigint')) {
    return Object.keys(record).length === 1 && typeof record.$bigint === 'string'
      && /^-?(?:0|[1-9]\d*)$/.test(record.$bigint) && record.$bigint !== '-0';
  }
  return Object.values(record).every(isJsonValue);
}

export function decodeJsonValue(value: JsonValue): JsonValue {
  const compressed = compressedValue(value);
  if (compressed) {
    try {
      const data = Buffer.from(compressed.$json, 'base64');
      if (data.toString('base64') !== compressed.$json) throw new Error('Invalid base64.');
      const decoded = inflateSync(data, { maxOutputLength: compressed.bytes });
      if (decoded.length !== compressed.bytes) throw new Error('Incorrect decoded byte count.');
      const parsed: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(decoded));
      if (!isJsonValue(parsed)) throw new Error('Decoded data must contain finite JSON values and canonical bigint tags.');
      return parsed;
    } catch {
      throw new Error('Invalid compressed problem value payload or byte count.');
    }
  }
  return value;
}

export function jsonValueBytes(value: JsonValue): number {
  const compressed = compressedValue(value);
  if (compressed) return compressed.bytes;
  if (Array.isArray(value)) return 2 + value.reduce<number>((bytes, item) => bytes + jsonValueBytes(item), 0) + Math.max(0, value.length - 1);
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value);
    return 2 + entries.reduce((bytes, [key, item]) =>
      bytes + Buffer.byteLength(JSON.stringify(key)) + 1 + jsonValueBytes(item), 0) + Math.max(0, entries.length - 1);
  }
  return Buffer.byteLength(JSON.stringify(value));
}

export function jsonValueSize(value: JsonValue): number {
  if (typeof value === 'string') {
    return value.length;
  }
  if (Array.isArray(value)) {
    return 1 + value.reduce<number>((size, item) => size + jsonValueSize(item), 0);
  }
  if (value !== null && typeof value === 'object') {
    if (typeof value.$bigint === 'string') {
      return 1;
    }
    return 1 + Object.values(value).reduce<number>((size, item) => size + jsonValueSize(item), 0);
  }
  return 1;
}
