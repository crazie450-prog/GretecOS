// The card catalog check, as gregtec.cards.validate does it, over the same schema file (D160).
// A card that fails it is never drawn: the dashboard shows why instead.
import schemaJson from "../../../core/gregtec/cards/schema.json";
import type { CardType } from "./types.gen";

interface Node {
  $ref?: string;
  oneOf?: Node[];
  anyOf?: Node[];
  const?: unknown;
  enum?: unknown[];
  type?: string | string[];
  properties?: Record<string, Node>;
  required?: string[];
  additionalProperties?: boolean;
  items?: Node;
  minItems?: number;
  maxItems?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  minimum?: number;
  maximum?: number;
}

const schema = schemaJson as unknown as Node & { $defs: Record<string, Node> };
const MAX_ERRORS = 20;

function resolve(node: Node): Node {
  let n = node;
  while (n.$ref) {
    if (!n.$ref.startsWith("#/$defs/")) throw new Error(`unsupported $ref ${n.$ref}`);
    n = schema.$defs[n.$ref.slice("#/$defs/".length)];
  }
  return n;
}

function isType(kind: string, v: unknown): boolean {
  switch (kind) {
    case "object":
      return typeof v === "object" && v !== null && !Array.isArray(v);
    case "array":
      return Array.isArray(v);
    case "string":
      return typeof v === "string";
    case "integer":
      return typeof v === "number" && Number.isInteger(v);
    case "number":
      return typeof v === "number" && Number.isFinite(v);
    case "boolean":
      return typeof v === "boolean";
    case "null":
      return v === null;
    default:
      return false;
  }
}

function check(node: Node, value: unknown, path: string, errors: string[]): void {
  if (errors.length >= MAX_ERRORS) return;
  const n = resolve(node);
  const union = n.oneOf ?? n.anyOf;
  if (union) {
    checkUnion(union, value, path, errors);
    return;
  }
  if ("const" in n && value !== n.const) {
    errors.push(`${path}: must be ${JSON.stringify(n.const)}`);
    return;
  }
  if (n.type) {
    const kinds = Array.isArray(n.type) ? n.type : [n.type];
    if (!kinds.some((k) => isType(k, value))) {
      errors.push(`${path}: must be ${kinds.join(" or ")}`);
      return;
    }
  }
  if (n.enum && !n.enum.includes(value)) {
    errors.push(`${path}: must be one of ${JSON.stringify(n.enum)}`);
    return;
  }
  if (typeof value === "string") {
    // Python counts code points; so does [...value].
    const length = [...value].length;
    if (n.maxLength !== undefined && length > n.maxLength) errors.push(`${path}: longer than ${n.maxLength} characters`);
    if (n.minLength !== undefined && length < n.minLength) errors.push(`${path}: shorter than ${n.minLength} characters`);
    if (n.pattern && !new RegExp(n.pattern, "u").test(value)) errors.push(`${path}: doesn't match ${n.pattern}`);
  } else if (typeof value === "number") {
    if (n.minimum !== undefined && value < n.minimum) errors.push(`${path}: below ${n.minimum}`);
    if (n.maximum !== undefined && value > n.maximum) errors.push(`${path}: above ${n.maximum}`);
  } else if (Array.isArray(value)) {
    if (n.maxItems !== undefined && value.length > n.maxItems) errors.push(`${path}: more than ${n.maxItems} items`);
    if (n.minItems !== undefined && value.length < n.minItems) errors.push(`${path}: fewer than ${n.minItems} items`);
    if (n.items) {
      const items = n.items;
      value.slice(0, n.maxItems ?? value.length).forEach((item, i) => check(items, item, `${path}[${i}]`, errors));
    }
  } else if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    const props = n.properties ?? {};
    for (const name of n.required ?? []) {
      if (!(name in record)) errors.push(`${path}.${name}: missing`);
    }
    if (n.additionalProperties === false) {
      for (const name of Object.keys(record)) {
        if (!(name in props)) errors.push(`${path}.${name}: not in the schema`);
      }
    }
    for (const [name, sub] of Object.entries(props)) {
      if (name in record) check(sub, record[name], `${path}.${name}`, errors);
    }
  }
}

function checkUnion(branches: Node[], value: unknown, path: string, errors: string[]): void {
  const resolved = branches.map(resolve);
  const consts: unknown[] = resolved.map((b) => b.properties?.type?.const);
  if (consts.every((c) => c !== undefined) && isType("object", value)) {
    const kind = (value as Record<string, unknown>).type;
    const i = consts.findIndex((c) => c === kind);
    if (i < 0) {
      errors.push(`${path}.type: unknown card type ${JSON.stringify(kind)}`);
      return;
    }
    check(resolved[i], value, path, errors);
    return;
  }
  for (const branch of resolved) {
    const attempt: string[] = [];
    check(branch, value, path, attempt);
    if (attempt.length === 0) return;
  }
  errors.push(`${path}: matches none of the allowed shapes`);
}

/** Why `card` isn't a catalog card; empty when it is. */
export function validate(card: unknown): string[] {
  const errors: string[] = [];
  check(schema, card, "card", errors);
  return errors;
}

/** The card types the schema lists, in catalog order. */
export function schemaCardTypes(): CardType[] {
  return (schema.oneOf ?? []).map((b) => resolve(b).properties?.type?.const as CardType);
}
