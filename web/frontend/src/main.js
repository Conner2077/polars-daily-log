import { createApp } from 'vue'
import ElementPlus, { ElMessage } from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './styles/global.css'

// ─── Toast (ElMessage) customization ─────────────────────────────────
// Default duration 1.5s (was 3s) + click anywhere on the toast dismisses it.
const TOAST_DURATION = 1500
;['success', 'warning', 'error', 'info'].forEach(method => {
  const orig = ElMessage[method]
  ElMessage[method] = function (opts) {
    const cfg = typeof opts === 'string' ? { message: opts } : { ...opts }
    if (cfg.duration == null) cfg.duration = TOAST_DURATION
    return orig.call(ElMessage, cfg)
  }
})

// Delegate click-to-dismiss: any .el-message element closes when clicked
document.addEventListener('click', (e) => {
  const msg = e.target.closest('.el-message')
  if (msg && !msg.classList.contains('is-closing')) {
    msg.classList.add('is-closing')
    // Trigger Element Plus's built-in close animation
    msg.dispatchEvent(new MouseEvent('mouseenter'))
    const closeBtn = msg.querySelector('.el-message__closeBtn')
    if (closeBtn) {
      closeBtn.click()
    } else {
      // Fallback: remove directly
      msg.style.transition = 'opacity 0.2s'
      msg.style.opacity = '0'
      setTimeout(() => msg.remove(), 200)
    }
  }
})

const app = createApp(App)
app.use(ElementPlus)
app.use(router)
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}
app.mount('#app')
