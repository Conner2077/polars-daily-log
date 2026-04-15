import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/activities', name: 'Activities', component: () => import('../views/Activities.vue') },
  { path: '/my-logs', name: 'MyLogs', component: () => import('../views/MyLogs.vue') },
  { path: '/chat', name: 'Chat', component: () => import('../views/Chat.vue') },
  { path: '/issues', name: 'Issues', component: () => import('../views/Issues.vue') },
  { path: '/settings', name: 'Settings', component: () => import('../views/Settings.vue') },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
