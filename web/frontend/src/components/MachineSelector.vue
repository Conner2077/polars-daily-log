<!--
MachineSelector — inline tag pills for filtering views by machine.

Hides itself entirely when there's only one (or zero) collectors —
users with a single machine shouldn't see a filter UI at all.
-->
<template>
  <div v-if="collectors.length > 1" class="machine-tags">
    <button
      v-if="includeAll"
      :class="['m-tag', { active: selected === null }]"
      @click="pick(null)"
    >
      <span class="m-icon">🌐</span>
      <span>全部</span>
      <span class="m-count">{{ collectors.length }}</span>
    </button>
    <button
      v-for="c in collectors"
      :key="c.machine_id"
      :class="['m-tag', { active: selected === c.machine_id }]"
      @click="pick(c.machine_id)"
      :title="c.hostname || ''"
    >
      <span class="m-icon">{{ platformIcon(c.platform) }}</span>
      <span>{{ c.name }}</span>
      <span :class="['m-dot', statusOf(c)]" />
    </button>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import api from '../api'

const props = defineProps({
  modelValue: { type: String, default: null },
  includeAll: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'change'])

const collectors = ref([])
const selected = ref(props.modelValue)

function platformIcon(p) {
  if (!p) return '💻'
  if (p === 'macos') return '🖥'
  if (p === 'windows') return '🪟'
  if (p.startsWith('linux')) return '🐧'
  return '💻'
}

function statusOf(c) {
  if (c.is_paused) return 'paused'
  if (!c.last_seen) return 'offline'
  const last = new Date(c.last_seen.replace(' ', 'T') + 'Z').getTime()
  return Date.now() - last < 3 * 60 * 1000 ? 'online' : 'offline'
}

async function load() {
  try {
    const r = await api.getCollectors()
    collectors.value = r.data
  } catch { /* ignore */ }
}

function pick(mid) {
  selected.value = mid
  emit('update:modelValue', mid)
  emit('change', mid)
}

watch(() => props.modelValue, (v) => { selected.value = v })

onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.machine-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.m-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 980px;
  border: 1px solid var(--border, rgba(0, 0, 0, 0.08));
  background: var(--surface, #fff);
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary, #1d1d1f);
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: var(--font);
}

.m-tag:hover {
  background: rgba(0, 0, 0, 0.03);
}

.m-tag.active {
  background: var(--text-primary, #1d1d1f);
  color: #fff;
  border-color: var(--text-primary, #1d1d1f);
}

.m-icon {
  font-size: 14px;
  line-height: 1;
}

.m-count {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 980px;
  background: rgba(0, 0, 0, 0.08);
  color: inherit;
  font-size: 11px;
  font-weight: 600;
}

.m-tag.active .m-count {
  background: rgba(255, 255, 255, 0.2);
}

.m-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.m-dot.online { background: #34c759; }
.m-dot.offline { background: #aeaeb2; }
.m-dot.paused { background: #ff9f0a; }
.m-tag.active .m-dot.offline { background: rgba(255, 255, 255, 0.5); }
</style>
