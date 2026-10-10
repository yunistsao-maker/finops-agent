/**
 * Color follows the entity, never its rank. Groups and token types have fixed slots; other dimensions (model,
 * service, ...) get the next free slot the first time a value is seen in this browser session and keep it.
 * Six validated slots (see styles.css); anything past them is "other" gray.
 */
export const GROUPS = ['llm', 'infrastructure', 'networking', 'platform_services', 'support_and_tax', 'other'];
const TOKEN_TYPES = ['input', 'output', 'cache_read', 'cache_write', 'provisioned'];
const MAX_SLOTS = 6;

const LABELS: Record<string, Record<string, string>> = {
  group: {
    llm: 'LLM', infrastructure: 'Infrastructure', networking: 'Networking', platform_services: 'Platform services',
    support_and_tax: 'Support & tax', other: 'Other',
  },
  token_type: { input: 'Input', output: 'Output', cache_read: 'Cache read', cache_write: 'Cache write', provisioned: 'Provisioned' },
  category: {
    llm: 'LLM inference', ai_agent_platform: 'AI agent platform', compute: 'Compute', storage: 'Storage', database: 'Database',
    networking: 'Networking', observability: 'Observability', security_and_management: 'Security & management',
    analytics: 'Analytics', app_integration: 'App integration', support: 'Support', tax: 'Tax', other: 'Other',
  },
};

export const CATEGORY_GROUP: Record<string, string> = {
  llm: 'llm', ai_agent_platform: 'infrastructure', compute: 'infrastructure', storage: 'infrastructure',
  database: 'infrastructure', networking: 'networking', observability: 'platform_services',
  security_and_management: 'platform_services', analytics: 'platform_services', app_integration: 'platform_services',
  support: 'support_and_tax', tax: 'support_and_tax', other: 'other',
};

export function label(dim: string, key: string): string {
  if (dim === 'service') return key.replace(/ \(Amazon Bedrock Edition\)$/, ''); // Marketplace model names
  return LABELS[dim]?.[key] ?? key;
}

const seen = new Map<string, Map<string, number>>();

export function seriesColor(dim: string, key: string): string {
  if (key === '(other)' || key === '-' || key === '(unallocated)') return 'var(--series-other)';
  const fixed = dim === 'group' ? GROUPS : dim === 'token_type' ? TOKEN_TYPES : null;
  if (fixed) {
    const i = fixed.indexOf(key);
    return i >= 0 && i < MAX_SLOTS ? `var(--series-${i + 1})` : 'var(--series-other)';
  }
  if (dim === 'category' && CATEGORY_GROUP[key]) return seriesColor('group', CATEGORY_GROUP[key]);
  let slots = seen.get(dim);
  if (!slots) seen.set(dim, (slots = new Map()));
  if (!slots.has(key)) {
    if (slots.size >= MAX_SLOTS) return 'var(--series-other)';
    slots.set(key, slots.size + 1);
  }
  return `var(--series-${slots.get(key)})`;
}
