<!--
  TimelineChart.vue — Rolling-window activity timeline (SVG bar chart)

  Usage:
    <TimelineChart />
    <TimelineChart :hours="24" :bucket-minutes="60" :poll-interval="30000" />

  Design notes
  ------------
  • Window math: we compute a "window end" rounded UP to the next bucket boundary.
    When wall-clock crosses into a new bucket, we advance `windowEndMs` and
    translate the bar group left by one bucket-width, producing the
    "conveyor belt" shift. CSS transition on `transform` handles the animation.

  • Bar height animation: each bar is a <g transform="scaleY(k)"> with
    transform-origin on the bar's baseline (via `transform-box: fill-box` +
    `transform-origin: 50% 100%`). CSS transitions the transform so data
    refreshes morph smoothly rather than jumping. Chose this over animating
    <rect height> because SVG attribute animation is clunky without SMIL.

  • Tooltip is an absolutely-positioned <div> over the SVG wrapper — avoids
    SVG <foreignObject> quirks and lets Element Plus fonts/colors apply.

  • Uses the same `/api` baseURL as the project's axios default export;
    we instantiate a local axios client here because api/index.js only
    exports named methods (no raw instance).
-->

<template>
  <div class="timeline-chart" ref="containerEl">
    <!-- Error state -->
    <div v-if="error && !loading" class="tl-error">
      <span class="tl-error-msg">加载失败</span>
      <el-button size="small" @click="refetch">重试</el-button>
    </div>

    <!-- Chart SVG -->
    <svg
      v-else
      class="tl-svg"
      :viewBox="`0 0 ${VB_W} ${VB_H}`"
      preserveAspectRatio="none"
      style="width:100%;height:100%;"
      @mouseleave="hideTooltip"
    >
      <!-- Loading skeleton -->
      <g v-if="loading" class="tl-skeleton">
        <rect
          v-for="i in skeletonCount"
          :key="`sk-${i}`"
          :x="(i - 1) * bucketPxWidth + BAR_GAP"
          :y="VB_H - PAD_BOTTOM - skeletonHeight(i)"
          :width="Math.max(2, bucketPxWidth - BAR_GAP * 2)"
          :height="skeletonHeight(i)"
          fill="var(--ink-dim)"
          fill-opacity="0.15"
        />
      </g>

      <!-- Horizontal grid lines (3 lines: 33%, 66%, top) -->
      <g v-if="!loading" class="tl-grid">
        <line :x1="0" :x2="VB_W" :y1="VB_H - PAD_BOTTOM - chartHeight * 0.33" :y2="VB_H - PAD_BOTTOM - chartHeight * 0.33" stroke="var(--line)" stroke-width="0.5" />
        <line :x1="0" :x2="VB_W" :y1="VB_H - PAD_BOTTOM - chartHeight * 0.66" :y2="VB_H - PAD_BOTTOM - chartHeight * 0.66" stroke="var(--line)" stroke-width="0.5" />
        <line :x1="0" :x2="VB_W" :y1="PAD_TOP" :y2="PAD_TOP" stroke="var(--line)" stroke-width="0.5" />
      </g>

      <!-- Bars: one per bucket. idle > 50% of total → gray, else black -->
      <g v-if="!loading" class="tl-bars" :style="{ transform: `translateX(${shiftPx}px)` }">
        <g
          v-for="(b, idx) in buckets"
          :key="b.start"
          class="tl-bar-cell"
          :transform="`translate(${idx * bucketPxWidth}, 0)`"
          :style="idx === 0 && shifting ? { opacity: 0 } : {}"
          @mouseenter="(e) => showTooltip(e, b, idx)"
        >
          <rect
            v-if="totalMins(b) > 0"
            :class="['tl-bar', isIdleDominant(b) ? 'tl-bar-idle' : 'tl-bar-active', { 'tl-bar-pulse': idx === buckets.length - 1 }]"
            :x="BAR_GAP"
            :y="VB_H - PAD_BOTTOM - barPx(b)"
            :width="barW"
            :height="barPx(b)"
            rx="1.5"
            :fill="'var(--ink)'"
            :fill-opacity="isIdleDominant(b) ? 0.22 : 1"
          />

          <!-- Empty → invisible hit area -->
          <rect
            v-else
            :x="BAR_GAP"
            :y="VB_H - PAD_BOTTOM - 4"
            :width="barW"
            :height="4"
            fill="transparent"
          />
        </g>
      </g>

      <!-- X-axis hour labels -->
      <g v-if="!loading" class="tl-axis">
        <text
          v-for="tick in xTicks"
          :key="`tick-${tick.label}-${tick.x}`"
          :x="tick.x"
          :y="VB_H - 6"
          text-anchor="middle"
          class="tl-tick"
        >
          {{ tick.label }}
        </text>
      </g>

      <!-- Current-time cursor — thin dashed line from top to baseline -->
      <g v-if="!loading && cursorX > 0 && cursorX < VB_W" class="tl-cursor" :transform="`translate(${cursorX}, 0)`">
        <line
          :x1="0"
          :x2="0"
          :y1="PAD_TOP"
          :y2="VB_H - PAD_BOTTOM"
          stroke="var(--ink-dim)"
          stroke-width="0.8"
          stroke-dasharray="3 3"
        />
        <circle :cx="0" :cy="PAD_TOP" r="2.5" fill="var(--ink-muted)" />
      </g>

      <!-- Empty-state label -->
      <text
        v-if="!loading && isEmpty"
        class="tl-empty"
        :x="VB_W / 2"
        :y="VB_H / 2"
        text-anchor="middle"
      >
        暂无活动
      </text>
    </svg>

    <!-- HTML tooltip overlay -->
    <div
      v-show="tip.visible"
      class="tl-tooltip"
      :style="{ left: `${tip.x}px`, top: `${tip.y}px` }"
    >
      <div class="tl-tooltip-range">{{ tip.range }}</div>
      <div class="tl-tooltip-body">{{ tip.body }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import axios from 'axios'

const props = defineProps({
  hours: { type: Number, default: 12 },
  bucketMinutes: { type: Number, default: 15 },
  pollInterval: { type: Number, default: 60000 },
})

// ─── SVG geometry ────────────────────────────────────────────────────────────
const VB_W = 960
const VB_H = 240
const PAD_BOTTOM = 24 // reserved for axis labels
const PAD_TOP = 16
const chartHeight = VB_H - PAD_BOTTOM - PAD_TOP // drawable vertical space
const BAR_GAP = 1.5 // px gap each side of a bar (total gap = 3px between bars)

// ─── State ──────────────────────────────────────────────────────────────────
const containerEl = ref(null)
const buckets = ref([]) // from API: [{ start, end, active_mins, idle_mins, top_app }]
const loading = ref(true)
const error = ref(null)
const windowEndMs = ref(Date.now()) // right-edge of chart (ms since epoch)
const nowMs = ref(Date.now())
const shiftPx = ref(0) // transient offset for boundary shift animation
const shifting = ref(false)

const tip = reactive({ visible: false, x: 0, y: 0, range: '', body: '' })

// ─── Derived ────────────────────────────────────────────────────────────────
const bucketParam = computed(() => {
  const n = props.bucketMinutes
  return n >= 60 ? '1h' : `${n}m`
})

const windowMs = computed(() => props.hours * 60 * 60 * 1000)
const bucketMs = computed(() => props.bucketMinutes * 60 * 1000)
const expectedBucketCount = computed(() =>
  Math.max(1, Math.round(windowMs.value / bucketMs.value)),
)
const bucketPxWidth = computed(() => VB_W / expectedBucketCount.value)

// Full bar width = bucket slot minus gaps on each side
const barW = computed(() => Math.max(3, bucketPxWidth.value - BAR_GAP * 2))

const maxActive = computed(() => {
  let m = 0
  for (const b of buckets.value) if ((b.active_mins || 0) > m) m = b.active_mins
  return m
})


const isEmpty = computed(
  () => buckets.value.length > 0 && buckets.value.every((b) => (b.active_mins || 0) === 0),
)

const skeletonCount = computed(() => expectedBucketCount.value)

function skeletonHeight(i) {
  // Deterministic pseudo-random pattern so skeleton looks natural
  const v = Math.sin(i * 12.9898) * 43758.5453
  const frac = v - Math.floor(v)
  return 20 + frac * (chartHeight * 0.6)
}

function barMode(b) {
  if ((b.active_mins || 0) > 0) return 'active'
  if ((b.idle_mins || 0) > 0) return 'idle'
  return 'empty'
}

// Max total (active + idle) across all buckets — used as the 100% height reference
const maxTotal = computed(() => {
  let m = 0
  for (const b of buckets.value) {
    const t = (b.active_mins || 0) + (b.idle_mins || 0)
    if (t > m) m = t
  }
  return m || 1
})

function totalMins(b) { return (b.active_mins || 0) + (b.idle_mins || 0) }
function isIdleDominant(b) { return (b.idle_mins || 0) > totalMins(b) * 0.5 }
// Bar height based on active_mins only (idle just determines color).
// For idle-dominant buckets, use idle_mins for height so they're visible but capped.
function barPx(b) {
  const active = b.active_mins || 0
  const idle = b.idle_mins || 0
  if (active > 0) {
    return (active / maxActive.value) * chartHeight * 0.88
  }
  // Idle-only: show a short bar (capped at 30% of chart height)
  const idleRatio = Math.min(1, idle / props.bucketMinutes)
  return idleRatio * chartHeight * 0.3
}

// Keep for tooltip
function activeScale(b) {
  return maxActive.value > 0 ? (b.active_mins / maxActive.value) * 0.9 : 0
}

// Cursor position inside the window
const cursorX = computed(() => {
  const windowStart = windowEndMs.value - windowMs.value
  const clamped = Math.min(Math.max(nowMs.value, windowStart), windowEndMs.value)
  const frac = (clamped - windowStart) / windowMs.value
  return frac * VB_W
})

// X-axis ticks: every ~2 hours, snapped to the nearest hour boundary
const xTicks = computed(() => {
  const ticks = []
  const windowStart = windowEndMs.value - windowMs.value
  const stepH = props.hours <= 6 ? 1 : props.hours <= 24 ? 2 : 6
  const stepMs = stepH * 60 * 60 * 1000
  // Find first hour boundary >= windowStart
  const d = new Date(windowStart)
  d.setMinutes(0, 0, 0)
  let t = d.getTime()
  if (t < windowStart) t += 60 * 60 * 1000
  while (t <= windowEndMs.value) {
    const frac = (t - windowStart) / windowMs.value
    const x = frac * VB_W
    const dt = new Date(t)
    const hh = String(dt.getHours()).padStart(2, '0')
    const mm = String(dt.getMinutes()).padStart(2, '0')
    ticks.push({ x, label: `${hh}:${mm}` })
    t += stepMs
  }
  return ticks
})

// ─── Fetch ──────────────────────────────────────────────────────────────────
let abortCtrl = null
let pollTimer = null
let cursorTimer = null
let lastBucketKey = null

async function refetch() {
  // Cancel in-flight request
  if (abortCtrl) abortCtrl.abort()
  abortCtrl = new AbortController()

  error.value = null
  try {
    const res = await axios.get('/api/activities/timeline', {
      params: { hours: props.hours, bucket: bucketParam.value },
      signal: abortCtrl.signal,
    })
    const data = res.data || {}
    const bucketMins = data.bucket_minutes || props.bucketMinutes
    // Map API shape (bucket_start) → internal shape (start/end as ISO strings)
    const incoming = (Array.isArray(data.buckets) ? data.buckets : []).map((b) => {
      const s = new Date(b.bucket_start)
      const e = new Date(s.getTime() + bucketMins * 60000)
      return { ...b, start: b.bucket_start, end: e.toISOString() }
    })

    // Detect bucket-boundary advance to trigger shift animation
    const newestStart = incoming.length ? incoming[incoming.length - 1].start : null
    const hadPrev = lastBucketKey !== null
    const advanced = hadPrev && newestStart && newestStart !== lastBucketKey

    if (advanced && buckets.value.length > 0) {
      // Run the shift animation, then swap in new data.
      shifting.value = true
      shiftPx.value = -bucketPxWidth.value
      await new Promise((resolve) => setTimeout(resolve, 600))
      shifting.value = false
      shiftPx.value = 0
    }

    buckets.value = incoming
    lastBucketKey = newestStart
    // Right edge: snap to the end of the newest bucket (or now if empty)
    if (incoming.length) {
      const last = incoming[incoming.length - 1]
      windowEndMs.value = new Date(last.end).getTime() || Date.now()
    } else {
      windowEndMs.value = Date.now()
    }
    loading.value = false
  } catch (e) {
    if (axios.isCancel(e) || e.name === 'CanceledError' || e.name === 'AbortError') {
      return // aborted; silent
    }
    error.value = e
    loading.value = false
  }
}

// ─── Tooltip ────────────────────────────────────────────────────────────────
function showTooltip(ev, b) {
  const host = containerEl.value
  if (!host) return
  const rect = host.getBoundingClientRect()
  const x = ev.clientX - rect.left + 10
  const y = ev.clientY - rect.top - 8
  const start = new Date(b.start)
  const end = new Date(b.end)
  const fmt = (d) =>
    `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const active = (b.active_mins || 0).toFixed(1)
  const topApp = b.top_app || '无'
  const mode = barMode(b)
  let body
  if (mode === 'active') body = `${active} min active · top: ${topApp}`
  else if (mode === 'idle') body = `${(b.idle_mins || 0).toFixed(1)} min idle`
  else body = '无活动'
  tip.range = `${fmt(start)}–${fmt(end)}`
  tip.body = body
  tip.x = x
  tip.y = y
  tip.visible = true
}

function hideTooltip() {
  tip.visible = false
}

// ─── Lifecycle ──────────────────────────────────────────────────────────────
onMounted(async () => {
  await refetch()
  pollTimer = setInterval(refetch, props.pollInterval)
  // Cursor ticks once per minute independently of the data poll
  cursorTimer = setInterval(() => {
    nowMs.value = Date.now()
  }, 60 * 1000)
})

onBeforeUnmount(() => {
  if (abortCtrl) abortCtrl.abort()
  if (pollTimer) clearInterval(pollTimer)
  if (cursorTimer) clearInterval(cursorTimer)
})

// React to prop changes (e.g. window size switch)
watch(
  () => [props.hours, props.bucketMinutes],
  async () => {
    loading.value = true
    buckets.value = []
    lastBucketKey = null
    await nextTick()
    refetch()
  },
)

watch(
  () => props.pollInterval,
  (v) => {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = setInterval(refetch, v)
  },
)
</script>

<style scoped>
.timeline-chart {
  position: relative;
  width: 100%;
  min-height: 220px;
  font-family: var(--font);
}

.tl-svg {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 220px;
  overflow: visible;
}

/* Bars ---------------------------------------------------------------- */
.tl-bars {
  transition: transform 0.6s ease;
}

.tl-bar {
  /* Scale from baseline (bottom of bar) */
  transform-box: fill-box;
  transform-origin: 50% 100%;
  transition: transform 0.4s ease, opacity 0.6s ease;
}

.tl-bar-cell {
  transition: opacity 0.6s ease;
}

.tl-bar-pulse {
  animation: tl-pulse 2.4s ease-in-out infinite alternate;
}

@keyframes tl-pulse {
  from { opacity: 0.9; }
  to { opacity: 1; }
}

/* Axis ---------------------------------------------------------------- */
.tl-tick {
  font-family: var(--font-mono);
  font-size: 11px;
  fill: var(--ink-dim);
}

.tl-empty {
  font-family: var(--font);
  font-size: 13px;
  fill: var(--ink-muted);
  letter-spacing: 0.04em;
}

/* Cursor -------------------------------------------------------------- */
.tl-cursor {
  pointer-events: none;
  transition: transform 0.4s ease;
}

/* Skeleton ------------------------------------------------------------ */
.tl-skeleton rect {
  animation: tl-skeleton-fade 1.6s ease-in-out infinite alternate;
}

@keyframes tl-skeleton-fade {
  from { opacity: 0.4; }
  to   { opacity: 0.9; }
}

/* Tooltip ------------------------------------------------------------- */
.tl-tooltip {
  position: absolute;
  pointer-events: none;
  background: var(--ink);
  color: var(--bg);
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 6px;
  line-height: 1.4;
  white-space: nowrap;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  transform: translateY(-100%);
  z-index: 10;
}

.tl-tooltip-range {
  font-family: var(--font-mono);
  font-size: 11px;
  opacity: 0.75;
  margin-bottom: 2px;
}

.tl-tooltip-body {
  font-family: var(--font);
}

/* Error --------------------------------------------------------------- */
.tl-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 220px;
  color: var(--ink-muted);
  font-size: 13px;
}

.tl-error-msg {
  color: var(--ink-muted);
}
</style>
